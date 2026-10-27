# Modules

Each module does one job. "Must not" lists what belongs elsewhere.
Nautilus-provided modules (IB clients, Data/Execution/Risk Engines, Cache, Portfolio, Message Bus)
are configured in `execsys/app.py` and described in `Diagrams/System_V2_Architecture.xlsx`.

---

## 1. Config: `execsys/config.py`, `config/settings.yaml`

- **Job:** load and validate all settings into frozen pydantic models; overlay secrets from env vars.
- **Validates:** `client_id` not a multiple of 1000, unique strategy ids, types/decimals.
- **Derived:** `settings.universe` (all instruments of enabled strategies), `settings.max_position(iid)`.
- **Must not:** hold secrets in YAML; be read anywhere except at startup.

## 2. Contracts: `execsys/contracts.py`

- **Job:** topic names and frozen message dataclasses (`Signal`, `RiskStateChanged`, `OrderIssue`, `Alert`,
  enums `RiskState`, `Severity`). The only thing modules share.
- **Rule:** change a contract → update [architecture.md](architecture.md#topics-and-contracts-execsyscontractspy).

## 3. App / node bootstrap: `execsys/app.py`

- **Job:** settings → telemetry → `LiveNode` (IB data + execution clients on one `client_id`, Risk Engine
  limits, JSON file logging, reconciliation) → register components → `node.run()` → flush telemetry.
- **Order of registration:** Monitoring, Recorder, strategies, Risk Monitor, Order Manager.
- **Flags:** `--config PATH`, `--data-only` (no execution client, no Risk Monitor/Order Manager).
- **Must not:** contain trading logic.

## 4. Strategies: `execsys/strategies/`

- **Job:** market data in → `Signal` out. One class per strategy, configured per entry in `strategies:`.
- **Base:** `SignalStrategy(DataActor)` gives `emit()` (dedupes repeated directions, records metrics,
  publishes on `app.signal.<id>`), `guard()` (per-ticker exception isolation), `load_instruments()`.
- **Included:** `EmaCrossSignal`: EMA fast/slow crossover on IB 1-minute bars, warm-up from history,
  signals only from live bars, per-ticker duplicate-bar guard.
- **Must not:** place orders (it has no order API), size positions, do I/O, call other strategies.
- **Failure:** an exception in one ticker is logged + counted (`execsys.handler.errors`); others continue.

## 5. Order Manager: `execsys/order_manager/`

- **Job:** the only component that trades.
  1. `SignalBook`: latest target per (instrument, strategy); net target = Σ direction × size.
  2. `plan_order`: clamp to max position; apply risk state (HALTED → nothing, REDUCING → only toward flat);
     **no new order while one is working** for the instrument (prevents duplicates/stacking);
     delta = desired − position.
  3. Throttle per instrument (`min_order_interval_ms`); re-evaluates when the window ends.
  4. Build MARKET or marketable LIMIT (`touch ± limit_offset_ticks`), `DAY`, reduce-only when REDUCING.
  5. `VirtualBook.allocate_fill`: split each fill to strategies by their gap to target → per-strategy
     position, average cost, realised/unrealised PnL (`strategy_pnl()`). Leftovers → `_UNATTRIBUTED`.
  6. Persist SignalBook + VirtualBook to `order_manager.state_file` (background thread) and restore on start.
- **Inputs:** `app.signal.*`, `app.risk.state`, order events, Portfolio, Cache quotes.
- **Outputs:** `submit_order`, `cancel_all_orders`, `close_all_positions`, `app.order.issue`, `app.alert`.
- **Re-plans on:** new signal, fill, cancel, expiry, risk state change.
- **Ignores:** signals from unknown strategies/instruments, signals older than `signal_stale_secs`,
  out-of-order signals.
- **On stop:** cancels working orders; flattens if `flatten_on_stop`.
- **Must not:** contain alpha logic or account-level limits.

## 6. Pre-trade risk (Nautilus Risk Engine): configured in `app.py`

- **Job:** every order: precision, `max_notional_per_order`, submit/modify rate (`40/00:00:01`, below IB's
  ~50 msg/s), reduce-only. Denials come back as `OrderDenied` to the Order Manager.

## 7. Risk Monitor: `execsys/risk/`

- **Job:** account-level risk on a timer (`check_interval_ms`), via pure `rules.evaluate`:

  | Rule | State |
  |---|---|
  | daily PnL ≤ −`max_daily_loss` | HALTED |
  | denials+rejects in `reject_window_secs` ≥ `max_rejects_per_window` | HALTED |
  | \|position\| > max position for that instrument | REDUCING |
  | gross exposure > `max_gross_exposure` | REDUCING |
  | holding a position and no quote/trade/bar for `stale_data_secs` | REDUCING |
  | manual `halt` / `reduce` / `flatten` | HALTED / REDUCING / HALTED + flatten |

- **Latching:** HALTED stays until `python -m execsys.ops resume` (or a new UTC day for loss-based halts).
- **Daily PnL:** Portfolio total PnL minus the baseline taken at the first check of the UTC day
  (persisted in `ops.risk_state_file`).
- **Fail-safe:** an exception during a check → REDUCING.
- **Outputs:** `app.risk.state` on change, CRITICAL `app.alert`, gauges `risk_state`, `daily_pnl`, `gross_exposure`.
- **Why a Strategy:** `DataActor` has no portfolio access. It never sends orders.

## 8. Data Recorder: `execsys/persistence/recorder.py`, `writer.py`

- **Job:** subscribe quotes, trades, bars for the universe; convert to string tuples (exact prices);
  `put_nowait` on a bounded queue. `BatchWriter` thread writes every `batch_size` rows or
  `flush_interval_secs` to `CsvSink`: `data/recorded/<UTC date>/{tick,bar}.csv`.
- **Tick schema** (compact, from `Diagrams/Modules.xlsx`): `schema, tick_type, asset_class, instrument_id,
  ts_event_utc, ts_init_ns, mid_price, last_price, last_size, aggressor_side`.
- **Bar schema:** `schema, bar_type, asset_class, instrument_id, ts_event_utc, ts_init_ns, open, high, low, close, volume`.
- **Back-pressure:** queue full → row dropped + `execsys.recorder.dropped`; sink errors → 3 retries, then dropped and counted.
- **On stop:** drains and flushes the queue.
- **Next:** a TimescaleDB/Postgres sink implementing the same `Sink` protocol.

## 9. State Store: `execsys/persistence/state_store.py`

- **Job:** `load()` at start; `save(dict)` is non-blocking and coalesced; the background thread writes atomically
  (`tmp` + `os.replace`). Used by Order Manager and Risk Monitor.

## 10. Telemetry: `execsys/telemetry/otel.py`

- **Job:** configure OTel tracer/meter/logger providers with OTLP gRPC exporters (or console), JSON
  Python logs in `logs/execsys.jsonl`. Provides `get_metrics()` and `get_order_tracer()`.
- **No-op until configured**, so tests and tools run without a collector.
- Details: [observability.md](observability.md).

## 11. Monitoring + Alerting: `execsys/alerting/`

- **MonitoringActor:** subscribes `app.alert`, IB socket state, event-queue pressure; sends start/stop notices.
- **AlertDispatcher:** queue → worker thread → `AlertFilter` (dedupe `dedupe_secs`, `max_per_minute`, CRITICAL
  bypasses rate limit) → channels: log file always, Telegram if `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` are set.

## 12. Ops CLI: `execsys/ops/__main__.py`

- `halt`, `reduce`, `flatten`, `resume` write `runtime/control.json` atomically; the Risk Monitor applies it at its
  next check (one `stat()` per check). `status` prints the persisted risk/Order Manager state.
