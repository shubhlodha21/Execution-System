# Execution System documentation

| Document | What it covers |
|---|---|
| [architecture.md](architecture.md) | The big picture: components, message flows, threading model, topics |
| [modules.md](modules.md) | Every module: job, inputs, outputs, what it must not do, files, failure handling |
| [setup_and_run.md](setup_and_run.md) | Install, IB Gateway, running live/data-only, operator commands |
| [configuration.md](configuration.md) | Every setting in `config/settings.yaml` and the env vars |
| [observability.md](observability.md) | OpenTelemetry: metric names, traces, logs, Grafana |
| [adding_a_strategy.md](adding_a_strategy.md) | How to write and register a new strategy |
| [decisions.md](decisions.md) | Design decisions and why (ADR log) |
| [roadmap.md](roadmap.md) | Phase status, known limitations, next steps |
| [changelog.md](changelog.md) | What changed, newest first |

The original drawings and module spreadsheets are kept locally in `Diagrams/` (git-ignored, not published).
