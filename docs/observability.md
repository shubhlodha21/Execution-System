# Observability (OpenTelemetry)

`execsys/telemetry/otel.py` sets up three OTel signals with resource attributes
`service.name=execsys`, `trader_id`, `ib.client_id`, exported over OTLP gRPC to `telemetry.otlp_endpoint`
(any OpenTelemetry collector or hosted backend). All export runs on SDK background threads.

If the collector is not reachable at startup, the app prints one warning and runs **without telemetry export**
for that run (trading is unaffected), instead of retrying and flooding the console. Start the collector and
restart the app to enable it. Use `127.0.0.1`, not `localhost`: on some hosts `localhost` resolves to IPv6 `::1`.

## Metrics

| Name | Type | Attributes | Meaning |
|---|---|---|---|
| `execsys.signals` | counter | strategy_id, instrument_id | signals published |
| `execsys.latency.data_to_signal` | histogram ms | strategy_id, instrument_id | market data `ts_event` → signal |
| `execsys.latency.signal_to_submit` | histogram ms | instrument_id | signal → `submit_order` |
| `execsys.latency.submit_to_fill` | histogram ms | instrument_id | submit → fill (includes IB round trip) |
| `execsys.orders.submitted` | counter | instrument_id, side | |
| `execsys.orders.denied` | counter | instrument_id | Nautilus Risk Engine denials |
| `execsys.orders.rejected` | counter | instrument_id | IB rejections |
| `execsys.fills` | counter | instrument_id | |
| `execsys.handler.errors` | counter | strategy_id, instrument_id | exceptions caught in handlers |
| `execsys.recorder.written` / `.dropped` | counter | | recorder rows |
| `execsys.alerts` | counter | severity, module | |
| `execsys.gauge` | gauge | `name` = `risk_state` (0 active, 1 reducing, 2 halted), `daily_pnl`, `gross_exposure`, `recorder_queue_depth` | |

Note: `execsys.latency.data_to_signal` for bar strategies includes the bar period (a bar's `ts_event` is its close).

## Traces

One span named `order` per order (`OrderTracer`): opened at submit with attributes `instrument_id`, `side`,
`quantity`, `target`, `position`, `risk_state`; `fill` events with qty/px; closed with `outcome`
(`filled`, `canceled`, `expired`, `denied: …`, `rejected: …`) and ERROR status for denials/rejections.

## Logs

- **Nautilus components** (all actors/strategies via `self.log`): JSON files in `logs/` from the node's logger.
- **Python side** (`execsys.*` loggers: alerts, writers, telemetry): `logs/execsys.jsonl` (JSON lines with
  `trace_id` when inside a span) **and** exported to the collector as OTel logs.
- To ship Nautilus file logs to Loki, add a `filelog` receiver for `logs/*.json*` to a custom Collector config.

## Grafana starter queries

- Order latency p95: `histogram_quantile(0.95, sum by (le) (rate(execsys_latency_submit_to_fill_milliseconds_bucket[5m])))`
- Risk state: `execsys_gauge{name="risk_state"}`
- Rejects: `sum by (instrument_id) (increase(execsys_orders_rejected_total[15m]))`

(Exact Prometheus metric names depend on the collector's OTLP→Prometheus translation; check Grafana Explore.)
