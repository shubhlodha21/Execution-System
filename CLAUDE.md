# Execution System: guide for Claude and developers

Multi-strategy algo-trading execution system for **Interactive Brokers**, built on
**NautilusTrader v2** (Rust core, Python API) with **OpenTelemetry** for traces, metrics and logs.
Everything we write is Python.

Full documentation lives in [docs/](docs/README.md). This file holds the rules that must never be broken
and the commands needed day to day.

## Keep the docs current (mandatory)

Every change to code or config updates the matching docs **in the same change**:

| You changed | Update |
|---|---|
| A module's behaviour, inputs or outputs | [docs/modules.md](docs/modules.md) |
| Message flow, topics, contracts, component wiring | [docs/architecture.md](docs/architecture.md) |
| A setting in `config/settings.yaml` / `execsys/config.py` | [docs/configuration.md](docs/configuration.md) |
| A metric, span or log field | [docs/observability.md](docs/observability.md) |
| A design choice with trade-offs | add an entry to [docs/decisions.md](docs/decisions.md) |
| Anything user-visible | add a line to [docs/changelog.md](docs/changelog.md) |
| Phase progress, known gaps | [docs/roadmap.md](docs/roadmap.md) |

## Environment

- Python 3.14, venv **outside OneDrive**: `C:\Users\shubham\.venvs\execution_system`
  (OneDrive syncing a venv is slow and can corrupt it).
- NautilusTrader **2.0.0rc7** from PyPI (same version as the source checkout in
  `D:\nautilus_trader_execution`, used as API reference: `python/nautilus_trader/**/*.pyi` stubs and `docs/`).
  v1 (`TradingNode`, Cython) APIs do **not** apply; never mix v1 and v2 in one venv.
- IB Gateway paper on `127.0.0.1:4002`, `client_id` 21.

```bash
# from the project root
/c/Users/shubham/.venvs/execution_system/Scripts/python -m pytest -q          # all tests (no IB needed)
/c/Users/shubham/.venvs/execution_system/Scripts/python -m execsys.app        # run live (needs IB_ACCOUNT_ID)
/c/Users/shubham/.venvs/execution_system/Scripts/python -m execsys.app --data-only
/c/Users/shubham/.venvs/execution_system/Scripts/python -m execsys.ops status # halt|reduce|flatten|resume|status
/c/Users/shubham/.venvs/execution_system/Scripts/python -m execsys.tools.fx_monitor   # live FX quotes + cross-rate matrix
```

## Layout

```
execsys/
  config.py            typed settings (pydantic) from config/settings.yaml + env secrets
  contracts.py         topics + message dataclasses shared by all modules
  app.py               node bootstrap: LiveNode, IB clients, components (no trading logic)
  strategies/          alpha: DataActors that publish Signal (no order API)
  order_manager/       book.py (pure netting/attribution/planning) + manager.py (the only trader)
  risk/                rules.py (pure) + monitor.py (timer-driven, publishes RiskStateChanged)
  persistence/         recorder.py + writer.py (queue -> background writer), state_store.py
  telemetry/otel.py    OTel traces/metrics/logs setup, Metrics, OrderTracer
  alerting/            monitor.py (bus -> alerts) + dispatcher.py (threaded, dedupe, Telegram)
  ops/__main__.py      operator CLI (writes runtime/control.json)
  tools/               standalone tools with their own data-only node (fx_monitor: live FX matrix)
config/settings.yaml   the only place strategies, tickers and limits are defined
tests/                 unit tests for pure logic + backtest end-to-end wiring tests
docs/                  architecture, modules, config, observability, decisions, roadmap, changelog
Diagrams/              original drawings and spreadsheets (user-owned, read-only, local only: git-ignored)
```

## Rules (do not break)

1. **One process, one IB `client_id`.** Data and execution clients share one socket.
2. **Strategies never trade.** They subclass `SignalStrategy` (a `DataActor`) and call `emit()`.
   Only `OrderManager` calls `submit_order`.
3. **Modules talk only through `contracts.py`** (topics + frozen dataclasses via `publish_message` /
   `subscribe_topic`). No module imports another module's actor class.
4. **The message bus is synchronous and single-threaded.** No I/O, network, sleep or heavy maths in any
   `on_*` handler or topic callback. Disk/DB/HTTP go through a queue to a background thread
   (`BatchWriter`, `JsonStateStore`, `AlertDispatcher`).
5. **Exact numbers.** Prices and quantities are `Decimal` / Nautilus `Price`/`Quantity` / strings. Never
   store or compare money as float (floats only for metrics).
6. **Config, not code.** New strategy or ticker = entry in `config/settings.yaml`. Secrets only from
   env vars (`.env.example`); never commit account ids or tokens.
7. **Risk state is enforced by the Order Manager.** Nautilus v2 Python cannot switch the Risk Engine's
   trading state, so `RiskMonitor` publishes `RiskStateChanged` and `plan_order` applies it
   (see decisions.md D-003).
8. **Unique ids.** Every strategy needs a unique `strategy_id` and Nautilus `order_id_tag`; the registered
   id is `<strategy_id>-<order_id_tag>`.
9. **Pure logic stays pure.** `order_manager/book.py` and `risk/rules.py` import no Nautilus code and are
   unit-tested; put new decision logic there first.
10. **Tests before claims.** Run `pytest -q`; wiring changes need the backtest flow test
    (`tests/test_backtest_flow.py`) to pass.

## Nautilus v2 API notes (verified against rc7)

- Node: `LiveNode.builder(name, TraderId, Environment.LIVE)...build()`; `node.add_actor()`, `node.add_strategy()`, `node.run()`.
- `DataActor` has no `portfolio`; components that need PnL/positions subclass `Strategy`.
- `publish_signal` values are strings only; we use `publish_message(topic, obj)` for typed messages.
- Timers: `self.clock.set_timer(name, timedelta, callback=...)`, `set_time_alert_ns(...)`.
- Backtest venues require an explicit `fee_model`.
- IB adapter: `nautilus_trader.adapters.interactive_brokers` (`InteractiveBrokersDataClientConfig`, ...).
