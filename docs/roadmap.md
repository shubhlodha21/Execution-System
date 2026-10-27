# Roadmap and status

Phases follow the Roadmap sheet in `Diagrams/System_V2_Architecture.xlsx`.

| # | Phase | Status | Acceptance |
|---|---|---|---|
| 1 | Live data verified | **Code ready** (`--data-only`), not yet run against Gateway from this project | bars/quotes for all tickers, ts within ~1 s |
| 2 | Data recording | **Code ready** (CSV sink, tested) | rows exact, no blocking, survives sink errors |
| 3 | Signals logged | **Code ready** (`EmaCrossSignal`, backtest-tested) | signals match TWS chart; warm-up works |
| 4 | Order Manager on paper | **Code ready**, backtest-tested; paper run pending | signals → orders → fills; no duplicates; netting |
| 5 | Risk controls | **Code ready**, manual halt backtest-tested; paper breach test pending | forced breach → HALTED, orders blocked, alert |
| 6 | More strategies | **in progress**: RSI (FX + equities) added via config, backtest-tested | added via config only |
| 7 | Operations | partial (ops CLI, alerts) | kill/restart test: reconciliation + state restore correct |
| 8 | Live, small size | planned | after 1–2 weeks clean on paper |

## Next steps

1. Run `--data-only` against IB Gateway paper; confirm instruments load and bars arrive.
2. Full paper run; check order spans and latency metrics in Grafana.
3. Restart test: kill the process with an open position, restart, verify reconciliation + restored targets.
4. TimescaleDB/Postgres sink for the recorder (implements `Sink`).
5. Scheduler: market open/close, EOD flatten, skip IB's daily restart window.
6. Trade journal: write signals, orders and fills to the database for audit.

## Known limitations

- Daily PnL baseline is taken from Portfolio total PnL at the first check of the UTC day; after a mid-day
  restart, Portfolio PnL is rebuilt by reconciliation and may not match the saved baseline exactly.
- Daily reset uses the UTC date, not the exchange session.
- Stale-data guard reads bars only for strategies that set `params.bar_spec`.
- Python topic messages (`app.*`) are not mirrored to an external bus (multi-node needs Redis streams).
- Nautilus file logs are not yet shipped to Loki (needs a Collector `filelog` receiver).
- `nautilus_trader` 2.0.0rc7 is a release candidate.
