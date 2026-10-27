#!/usr/bin/env bash
# Start ALL enabled strategies (config/settings.yaml) plus the terminal dashboard in one tmux session.
#
#   scripts/start.sh               # paper trading (needs IB_ACCOUNT_ID in .env or the environment)
#   scripts/start.sh data-only     # signals only, no orders
#
# Pane 1: python -m execsys.app      Pane 2: python -m execsys.dashboard
# Detach: Ctrl+B then D     Re-attach: tmux attach -t execsys     Stop: scripts/stop.sh
set -euo pipefail

SESSION="execsys"
MODE="${1:-paper}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

die() { echo "ERROR: $*" >&2; exit 1; }

case "$MODE" in
  paper) APP_ARGS="" ;;
  data-only) APP_ARGS="--data-only" ;;
  *) die "unknown mode '$MODE' (use: paper | data-only)" ;;
esac

command -v tmux >/dev/null || die "tmux is not installed (sudo apt install tmux)"
[[ -f .venv/bin/activate ]] || die ".venv not found in $ROOT (python3 -m venv .venv && pip install --pre -e '.[dev]')"
# shellcheck disable=SC1091
source .venv/bin/activate

# Secrets such as IB_ACCOUNT_ID come from .env (never committed)
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi
if [[ "$MODE" == "paper" && -z "${IB_ACCOUNT_ID:-}" ]]; then
  die "IB_ACCOUNT_ID is not set: add it to .env (see .env.example) or run 'scripts/start.sh data-only'"
fi

# Settings summary and IB Gateway reachability check
read -r IB_HOST IB_PORT IB_CLIENT_ID STRATEGIES < <(python - <<'PY'
from execsys.config import load_settings
s = load_settings()
print(s.ib.host, s.ib.port, s.ib.client_id, ",".join(x.strategy_id for x in s.enabled_strategies) or "-")
PY
)
if ! python -c "import socket,sys; socket.create_connection(('$IB_HOST', $IB_PORT), timeout=3)" 2>/dev/null; then
  die "IB Gateway not reachable at $IB_HOST:$IB_PORT (start and log in to IB Gateway, API enabled)"
fi
if [[ "$IB_PORT" != "4002" && "$IB_PORT" != "7497" ]]; then
  echo "WARNING: port $IB_PORT is a LIVE trading port. Starting in 5 s, Ctrl+C to abort." >&2
  sleep 5
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
  die "tmux session '$SESSION' is already running: 'tmux attach -t $SESSION', or stop it with scripts/stop.sh"
fi

echo "Starting execsys ($MODE) on $IB_HOST:$IB_PORT, client_id $IB_CLIENT_ID"
echo "Strategies: $STRATEGIES"

ACTIVATE="cd '$ROOT' && source .venv/bin/activate"
tmux new-session -d -s "$SESSION" -n main "$ACTIVATE && python -m execsys.app $APP_ARGS; echo; echo 'execsys.app exited'; read -r"
sleep 3  # give the app a moment to write its first snapshot
tmux split-window -h -t "$SESSION:main" "$ACTIVATE && python -m execsys.dashboard"
tmux select-pane -t "$SESSION:main.1"

if [[ -t 1 ]]; then
  exec tmux attach -t "$SESSION"
else
  echo "Running in tmux session '$SESSION' (attach with: tmux attach -t $SESSION)"
fi
