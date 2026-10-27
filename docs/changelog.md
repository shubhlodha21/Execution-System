# Changelog

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
