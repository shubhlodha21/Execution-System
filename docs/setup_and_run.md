# Setup and run

## 1. Python environment (once)

The venv lives **outside OneDrive** so syncing does not slow it down or corrupt it.

```bash
python -m venv /c/Users/shubham/.venvs/execution_system
/c/Users/shubham/.venvs/execution_system/Scripts/python -m pip install --pre -e ".[dev]"
```

`pyproject.toml` pins `nautilus_trader==2.0.0rc7` (the v2 Rust/PyO3 package).

## 2. IB Gateway

- Paper: port **4002**, live: **4001**. Settings → API: enable socket clients, trusted IP 127.0.0.1,
  **Read-Only API off** for trading.
- Without market data subscriptions on the paper account, set `ib.market_data_type: delayed-frozen`.

## 3. Secrets

Copy `.env.example` to `.env` (git-ignored) and set at least `IB_ACCOUNT_ID` (the `DU...` paper account).
Load them into your shell before running, e.g. in PowerShell:

```powershell
Get-Content .env | Where-Object { $_ -match '^[A-Z_]+=' } | ForEach-Object { $k,$v = $_ -split '=',2; Set-Item "env:$k" $v }
```

## 4. Telemetry (optional)

Telemetry is exported over OTLP gRPC to `telemetry.otlp_endpoint` (default `http://127.0.0.1:4317`). Point it at
any OpenTelemetry collector or hosted backend you run. If nothing is listening there at startup, the app prints
one warning and runs without telemetry; trading is unaffected. To turn it off entirely, set
`telemetry.enabled: false`. Logs are always written to `logs/`.

## 5. Run

```bash
# data only: verify connection, instruments, bars, recording, signals (no orders)
/c/Users/shubham/.venvs/execution_system/Scripts/python -m execsys.app --data-only

# full system on paper
/c/Users/shubham/.venvs/execution_system/Scripts/python -m execsys.app
```

Stop with Ctrl+C: the Order Manager cancels working orders, writers flush, the socket closes.

## 6. Operator commands (from a second terminal)

```bash
/c/Users/shubham/.venvs/execution_system/Scripts/python -m execsys.ops halt      # no new orders
/c/Users/shubham/.venvs/execution_system/Scripts/python -m execsys.ops reduce    # only reduce positions
/c/Users/shubham/.venvs/execution_system/Scripts/python -m execsys.ops flatten   # halt + close everything
/c/Users/shubham/.venvs/execution_system/Scripts/python -m execsys.ops resume    # back to normal
/c/Users/shubham/.venvs/execution_system/Scripts/python -m execsys.ops status
```

## 7. Terminal dashboard

With the app running, open a second terminal (or tmux pane) in the project folder:

```bash
python -m execsys.dashboard
```

Shows client ID, PAPER/LIVE, connection, risk state, uptime, UTC/IST time, LTP and positions for every active ticker,
each strategy's timeframe, current signal per ticker, last bar and per-strategy PnL, and the latest signals and orders.
It reads `runtime/status.json`, so it opens no IB connection. `--once` prints a single screen.

## 7b. Live FX monitor (terminal)

```bash
python -m execsys.tools.fx_monitor                                  # EUR/USD, USD/CHF, USD/JPY
python -m execsys.tools.fx_monitor --pairs EUR/USD GBP/USD USD/JPY
```

Shows a live quote table and a cross-rate matrix, refreshed every second; Ctrl+C to quit. It uses `ib.client_id`
(99), the same as the trading app; to run both at once, start the monitor with another id, e.g. `--client-id 98`. FX quotes only flow while the FX market is open
(Sunday ~17:00 to Friday ~17:00 New York time).

## 8. Tests

```bash
/c/Users/shubham/.venvs/execution_system/Scripts/python -m pytest -q
```

No IB connection needed. `tests/test_backtest_flow.py` runs the real components on the Nautilus backtest
engine: bars → strategy → Order Manager → simulated venue fills, plus the manual-halt scenario.

## Files written at runtime

| Path | Content |
|---|---|
| `logs/nautilus_*.json*` | Nautilus component logs (JSON) |
| `logs/execsys.jsonl` | Python-side logs (alerts, writers, telemetry) |
| `data/recorded/<date>/tick.csv`, `bar.csv` | recorded market data |
| `runtime/order_manager_state.json` | signal targets + per-strategy virtual positions |
| `runtime/risk_state.json` | daily PnL baseline, latched halt, manual state |
| `runtime/control.json` | last operator command |
