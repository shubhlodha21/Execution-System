# Design decisions

Newest last. Each entry: context → decision → consequences.

## D-001 · NautilusTrader v2 (rc7), not v1
- **Context:** the reference checkout `D:\nautilus_trader_execution` and the architecture spreadsheets target
  the v2 Rust core with the `LiveNode` Python API. PyPI's default is still v1 (`TradingNode`, Cython).
- **Decision:** pin `nautilus_trader==2.0.0rc7` in its own venv (`C:\Users\shubham\.venvs\execution_system`).
- **Consequences:** release-candidate APIs may change; re-verify stubs on upgrade. Never install v1 in the same venv.

## D-002 · Strategies are DataActors, one Order Manager trades
- **Decision:** strategies subclass `SignalStrategy(DataActor)` (no order API) and publish target directions;
  `OrderManager(Strategy)` nets them and is the only component calling `submit_order`.
- **Consequences:** no duplicate/conflicting orders across strategies; fewer IB messages. Nautilus attributes
  every order to `ORDER-MANAGER-001`, so per-strategy PnL comes from our `VirtualBook`.

## D-003 · Risk state enforced by the Order Manager
- **Context:** the v2 Python API exposes no way to switch the Risk Engine's `TradingState`.
- **Decision:** the Risk Monitor publishes `RiskStateChanged` on `app.risk.state`; the Order Manager cancels
  working orders on non-ACTIVE states and `plan_order` blocks (HALTED) or limits to reducing orders (REDUCING,
  sent `reduce_only`). Per-order checks stay in the Nautilus Risk Engine.
- **Consequences:** protection holds because the Order Manager is the only trader (D-002). Revisit if v2 adds a
  Python trading-state command.

## D-004 · Typed messages over `publish_message`, not `publish_signal`
- **Context:** `publish_signal` carries only string values.
- **Decision:** frozen dataclasses on `app.*` topics via `publish_message`/`subscribe_topic` (delivered by
  reference, synchronously).
- **Consequences:** messages must be immutable; they are not mirrored to an external bus.

## D-005 · Risk Monitor is a Strategy that never trades
- **Context:** `DataActor` has no `portfolio`.
- **Decision:** subclass `Strategy` with id `RISK-MONITOR` and tag `RM` (order_id_tags must be unique).

## D-006 · Signals are target directions, not orders
- **Decision:** `Signal.direction ∈ {-1,0,1}` × configured `size` = strategy target; Order Manager orders
  `target − position`, never while another order works for that instrument.
- **Consequences:** idempotent (repeat signals are harmless), restart-safe (targets persisted), naturally nets.

## D-007 · Background threads for all I/O, file-based first
- **Decision:** CSV recorder, JSON state store, threaded alert dispatcher; each behind a small interface
  (`Sink`, `load/save`, channel callables) so Postgres/Redis/other channels can be swapped in later.

## D-008 · Venv outside OneDrive
- **Decision:** keep the venv in `C:\Users\shubham\.venvs\execution_system`; runtime outputs (`logs/`, `data/`,
  `runtime/`) are git-ignored. Consider moving `data/` off OneDrive once volumes grow.

## D-009 · Keep adapter SMART routing, no separate market-data module
- **Context:** market data should come from IB SMART routing, not one exchange.
- **Finding:** the IB adapter already sends stock contracts as `exchange=SMART` + `primaryExchange` (the `.XNAS`
  suffix is only the Nautilus id), for data and orders alike.
- **Decision:** no separate data module; keep the existing ids and document the behaviour.
- **Consequences:** if a non-SMART venue is ever needed, it would require explicit `load_contracts` or raw symbology.
