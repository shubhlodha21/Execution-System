# Changelog

## 2026-10-27: test dependency

- `pandas` added to the `dev` extras; the backtest tests need it for Nautilus fill reports (found on EC2).

## 2026-10-27: RSI strategy, FX support

- New `RsiSignal` strategy (overbought/oversold mean reversion with exit level), configured for FX
  (`RSI-FX-001`: EUR/USD, USD/JPY, 5-min MID bars, 20,000 units) and equities (`RSI-EQ-001`: AAPL, MSFT, 5-min bars).
- Risk Monitor converts PnL/exposure to `risk_limits.base_currency` (USD) before summing.
- Recorder records FX with MID bars and no trade stream.
- FX position limits (40,000) and `stale_data_secs: 360` for 5-minute bars.

## 2026-10-27: client_id 99

- Default IB `client_id` is now **99** for the trading app (`ib.client_id`) and the FX monitor (reads the same
  setting). One client_id serves all tickers over one connection; two processes cannot share it at the same time.

## 2026-10-27: live FX monitor

- Header shows the time in both UTC and IST.
- Default client_id is 99 (`--client-id` to change), separate from the trading app's 21.

- New `python -m execsys.tools.fx_monitor`: live FX quote table + cross-rate matrix in the terminal
  (default EUR/USD, USD/CHF, USD/JPY), own data-only node.
- `build_node()` accepts an optional `instruments` list (defaults to `settings.universe`).

## 2026-10-27: SMART routing documented

- Docs explain that stock ids like `AAPL.XNAS` already use IB SMART routing for data and orders
  (configuration.md, modules.md, decisions.md D-009). No code change.

## 2026-10-27: Docker removed

- Removed `deploy/docker-compose.yml` and all Docker instructions. Run IB Gateway/TWS directly; send telemetry to
  any OpenTelemetry collector or backend via `telemetry.otlp_endpoint`, or set `telemetry.enabled: false`.

## 2026-10-27: fixes from first EC2 run

- Strategy warm-up now requests an exact whole-day window with an explicit end (`warmup_window`). Before, the IB
  adapter added a 1-second segment that IB rejected with error 321 ("duration is invalid"); the bars from the
  main segment were still delivered.
- Telemetry: if the OTel collector is unreachable at startup, export is disabled for the run with one warning
  instead of repeated export errors. Default endpoint is now `http://127.0.0.1:4317` (avoids IPv6 `::1`).

## 2026-10-27: published to GitHub

- Pushed to https://github.com/shubhlodha21/Execution-System (public). `Diagrams/`, `logs/`, `runtime/`, `data/`
  and `.env` are git-ignored and not published. README expanded.

## 2026-10-26: initial build (v0.1.0)

- Project scaffold: `execsys` package, `config/settings.yaml`, `pyproject.toml`, venv on NautilusTrader 2.0.0rc7.
- Modules: config, contracts, app bootstrap (one `LiveNode`, IB data + execution on `client_id` 21),
  `SignalStrategy` base + `EmaCrossSignal`, Order Manager (netting, planning, throttling, per-strategy
  attribution, persisted state), Risk Monitor (daily loss, position, exposure, stale data, rejects,
  manual control, latching halt), Data Recorder (queue + background CSV writer), State Store,
  OpenTelemetry (metrics, order spans, logs), Monitoring actor + Alert dispatcher (dedupe, Telegram),
  ops CLI.
- Tests: 43 passing, including backtest end-to-end flow and manual-halt scenario.
- Docs: CLAUDE.md and `docs/`.
- `deploy/docker-compose.yml`: grafana/otel-lgtm, optional IB Gateway.
