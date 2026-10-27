# Architecture

## One node, one connection

Everything runs in **one Python process** hosting one NautilusTrader v2 `LiveNode`. The IB data
client and the IB execution client use the **same `client_id` (21)**, so they share **one TCP socket**
to IB Gateway. All components run on the node's **single event thread** and communicate over the
in-process **message bus**, so a message between modules is a function call, not a network hop.

```
                        IB Gateway 127.0.0.1:4002 (paper) · client_id 21 · one socket
                           ▲  subscriptions / orders          │  ticks, bars / order events
┌──────────────────────────┼──────────────────────────────────┼─────────────────────────────┐
│ LiveNode (one process,   │  one event thread)               ▼                             │
│                                                                                           │
│  IB Data Client ─► Data Engine ─► Cache ─► Message Bus ◄── Execution Engine ◄─ IB Exec Client
│                                              │   ▲                ▲      │                 │
│          ┌───────────────┬───────────────────┤   │                │      ▼                 │
│          ▼               ▼                   ▼   │                │   Portfolio            │
│   Data Recorder    Strategy 01..N      Monitoring│          Risk Engine (pre-trade)        │
│   (queue)          (DataActor)         Actor     │                ▲                        │
│      │               │ Signal                │   │                │ submit_order           │
│      │               └──► app.signal.* ──► Order Manager (Strategy) ─┘                     │
│      │                                       ▲   │                                         │
│      │               Risk Monitor ── app.risk.state (RiskStateChanged)                     │
│      │               (Strategy, timer)  ◄── app.order.issue (OrderIssue)                    │
│      ▼                                           ▼                                         │
│  BatchWriter thread                     AlertDispatcher thread        OTel SDK threads     │
└──────┼───────────────────────────────────────────┼─────────────────────────┼─────────────┘
       ▼                                           ▼                         ▼ OTLP gRPC :4317
  data/recorded/*.csv                  log file + Telegram           OTel Collector → Grafana
```

## Message flows

| Flow | Path |
|---|---|
| Market data | IB → Data Client → Data Engine → Cache + bus topics → Strategies, Recorder, Order Manager (quotes) |
| Signal → order | Strategy `emit()` → `app.signal.<strategy_id>` → Order Manager nets → `plan_order` → `submit_order` → Risk Engine → Execution Engine → IB |
| Order return | IB → Exec Client → Execution Engine → Cache/Portfolio → Order Manager `on_order_*` → VirtualBook attribution → re-plan |
| Risk | Risk Monitor timer (1 s) → `rules.evaluate` → `app.risk.state` → Order Manager (cancels working orders, gates new ones, optional flatten) + alert |
| Order problems | Order Manager `on_order_denied/rejected` → `app.order.issue` → Risk Monitor reject counter, and `app.alert` |
| Alerts | Any module → `app.alert` → Monitoring actor → AlertDispatcher queue → log/Telegram (worker thread) |
| Manual control | `python -m execsys.ops halt` → `runtime/control.json` → Risk Monitor (next check) → `app.risk.state` |
| Persistence | Recorder `on_*` → bounded queue → BatchWriter thread → CSV; OM/Risk state → JsonStateStore thread → `runtime/*.json` |
| Telemetry | Handlers record counters/histograms/spans → OTel SDK background exporters → OTLP → Collector |

## Topics and contracts (`execsys/contracts.py`)

| Topic | Message | Producer | Consumers |
|---|---|---|---|
| `app.signal.<strategy_id>` | `Signal(strategy_id, instrument_id, direction ∈ {-1,0,1}, strength, reason, ts_event, ts_init)` | strategies | Order Manager (`app.signal.*`) |
| `app.risk.state` | `RiskStateChanged(state, reasons, flatten, ts)` | Risk Monitor | Order Manager (priority 10) |
| `app.order.issue` | `OrderIssue(kind, instrument_id, reason, ts)` | Order Manager | Risk Monitor |
| `app.alert` | `Alert(severity, module, message, ts)` | any | Monitoring actor |

A signal is a **target direction**, not an order: the Order Manager multiplies it by the strategy's
configured `size`. Sending the same signal twice therefore never creates a second order.

## Threading model

| Thread | Runs | Rule |
|---|---|---|
| Event thread (Nautilus) | every `on_*` handler, timers, topic callbacks | must stay fast; no I/O |
| `recorder-writer` | CSV batch writes | fed by bounded queue; drops (and counts) when full |
| `state-store-*` | atomic JSON state writes | coalesces saves; latest snapshot wins |
| `alert-dispatcher` | log + Telegram delivery | dedupe + rate limit; retries |
| OTel SDK threads | span/metric/log export | batch processors; never block handlers |

## Component ids

| Component | Kind | Registered id |
|---|---|---|
| Monitoring actor | DataActor | `MONITORING-001` |
| Data Recorder | DataActor | `DATA-RECORDER-001` |
| Strategies | DataActor | their `strategy_id` (e.g. `EMA-CROSS-001`) |
| Risk Monitor | Strategy (no orders) | `RISK-MONITOR-RM` |
| Order Manager | Strategy | `ORDER-MANAGER-001` (`strategy_id` + `-` + `order_id_tag`) |

## Scaling path

1. **Now:** one node, N strategies, one account. CPU time on the event thread is the limit.
2. Heavy models → separate processes publishing signals (Redis/ZeroMQ); execution stays in this node.
3. Independent strategy groups → more nodes on the same Gateway, each with its own `client_id`.
4. Cross-node risk view / dashboards → Nautilus external message bus (Redis streams).

The full scalability, failure and data-flow tables are in `Diagrams/System_V2_Architecture.xlsx`.
