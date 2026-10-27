# Execution-System
Trading Execution System for IBKR

A multi-strategy execution system for Interactive Brokers, built on
[NautilusTrader](https://github.com/nautechsystems/nautilus_trader) v2 with OpenTelemetry for traces, metrics and logs.

- One process, one IB connection (`client_id` 99) shared by market data and execution.
- Strategies only publish signals; a single Order Manager nets them into orders.
- Account-level Risk Monitor (daily loss, position, exposure, stale data, rejects) with a manual kill switch.
- Non-blocking market data recorder, persisted restart state, alerting (Telegram).

```bash
pip install --pre -e ".[dev]"
python -m pytest -q                      # tests, no IB needed
python -m execsys.app --data-only        # connect to IB Gateway, no orders
python -m execsys.app                    # full system (needs IB_ACCOUNT_ID)
```

Start with [docs/README.md](docs/README.md) and [docs/setup_and_run.md](docs/setup_and_run.md).
