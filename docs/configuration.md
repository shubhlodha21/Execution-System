# Configuration

All settings live in `config/settings.yaml` and are validated by `execsys/config.py`.
Unknown keys are rejected. Decimals are written as strings (`"1000"`).

## Environment variables (override YAML; secrets go only here)

| Variable | Setting |
|---|---|
| `IB_ACCOUNT_ID` | `ib.account_id` (required unless `--data-only`) |
| `IB_HOST`, `IB_PORT`, `IB_CLIENT_ID` | `ib.host`, `ib.port`, `ib.client_id` |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | `alerts.telegram_*` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `telemetry.otlp_endpoint` |

## Sections

### `node`
| Key | Default | Meaning |
|---|---|---|
| `name`, `trader_id` | `EXECSYS`, `TRADER-001` | node identity (trader id must contain `-`) |
| `reconciliation` | `true` | align orders/positions with IB at startup |
| `reconciliation_lookback_mins` | `1440` | how far back reconciliation looks |
| `log_dir`, `log_level` | `logs`, `INFO` | Nautilus JSON logs + Python logs |

### `ib`
| Key | Default | Meaning |
|---|---|---|
| `host`, `port` | `127.0.0.1`, `4002` | Gateway (4002 paper, 4001 live) |
| `client_id` | `21` | shared by data + execution; not a multiple of 1000 |
| `market_data_type` | `realtime` | `realtime` / `frozen` / `delayed` / `delayed-frozen` |
| `connection_timeout_secs`, `request_timeout_secs` | `10`, `30` | |

### `risk_engine` (Nautilus pre-trade, every order)
| Key | Default | Meaning |
|---|---|---|
| `max_order_submit_rate`, `max_order_modify_rate` | `40/00:00:01` | node-wide; IB allows ~50 msg/s |
| `max_notional_per_order` | `{}` | `instrument_id: "notional"` fat-finger limit |

### `risk_limits` (Risk Monitor)
| Key | Default | Meaning |
|---|---|---|
| `max_daily_loss` | `1000` | HALTED at daily PnL ≤ −value |
| `default_max_position`, `max_position_per_instrument` | `100`, `{}` | REDUCING above; also clamps Order Manager targets |
| `max_gross_exposure` | `250000` | REDUCING above |
| `stale_data_secs` | `120` | REDUCING when holding a position with no fresh quote/trade/bar |
| `max_rejects_per_window`, `reject_window_secs` | `5`, `60` | HALTED on repeated denials/rejects |
| `flatten_on_halt` | `false` | also close positions when HALTED automatically |
| `check_interval_ms` | `1000` | timer interval |

### `order_manager`
| Key | Default | Meaning |
|---|---|---|
| `strategy_id`, `order_id_tag` | `ORDER-MANAGER`, `001` | registered as `ORDER-MANAGER-001` |
| `order_type` | `MARKET` | or `LIMIT` (marketable: touch ± `limit_offset_ticks`) |
| `signal_stale_secs` | `300` | ignore older signals |
| `min_order_interval_ms` | `500` | per-instrument throttle |
| `state_file` | `runtime/order_manager_state.json` | restart state |
| `flatten_on_stop` | `false` | close positions on shutdown |

### `strategies` (list)
| Key | Meaning |
|---|---|
| `strategy_id` | unique, contains `-` (e.g. `EMA-CROSS-001`) |
| `class_path` | `module:Class`, a `SignalStrategy` subclass |
| `enabled` | turn on/off without deleting |
| `instruments` | IB simplified symbology, e.g. `AAPL.XNAS`, `EUR/USD.IDEALPRO` |
| `size` | position size per instrument when direction is ±1 |
| `params` | strategy-specific; `bar_spec` is also used by the Risk Monitor's stale-data check |

### `recorder`, `telemetry`, `alerts`, `ops`
See the defaults and comments in `config/settings.yaml`; meanings are in [modules.md](modules.md).
