#!/usr/bin/env bash
# The one command to run: start ALL enabled strategies (config/settings.yaml) and show ONLY the dashboard.
# Run settings (account, IB port, client id, mode, ...) come from .env; on the first run .env is created
# from .env.example and you are asked for your account ID once. A running execsys is restarted cleanly.
#
#   scripts/start.sh               # mode from RUN_MODE in .env (paper = signals + orders)
#   scripts/start.sh data-only     # override: signals only, no orders
#
# tmux session "execsys": hidden window "app" runs `python run.py` (all strategies, logs),
# visible window "dashboard" runs `python -m execsys.dashboard` full screen.
# Detach: Ctrl+B then D   Re-attach: tmux attach -t execsys   App logs: Ctrl+B then n   Stop: scripts/stop.sh
set -euo pipefail

SESSION="execsys"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

die() { echo "ERROR: $*" >&2; exit 1; }

command -v tmux >/dev/null || die "tmux is not installed (sudo apt install tmux)"
[[ -f .venv/bin/activate ]] || die ".venv not found in $ROOT (python3 -m venv .venv && pip install --pre -e '.[dev]')"
# shellcheck disable=SC1091
source .venv/bin/activate

# All run settings live in .env (never committed)
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example (edit it any time to change the run settings)"
fi
set -a
# shellcheck disable=SC1091
source .env
set +a

MODE="${1:-${RUN_MODE:-paper}}"
case "$MODE" in
  paper) APP_ARGS="" ;;
  data-only) APP_ARGS="--data-only" ;;
  *) die "unknown mode '$MODE' (use: paper | data-only, in RUN_MODE or as the argument)" ;;
esac

if [[ "$MODE" == "paper" && ( -z "${IB_ACCOUNT_ID:-}" || "${IB_ACCOUNT_ID}" == "DU0000000" ) ]]; then
  [[ -t 0 ]] || die "IB_ACCOUNT_ID is not set in .env (or run with RUN_MODE=data-only)"
  read -rp "Enter your IB account ID (paper DU..., live U...): " ACCOUNT
  ACCOUNT="${ACCOUNT//[[:space:]]/}"
  [[ "$ACCOUNT" =~ ^D?U[0-9]+$ ]] || die "'$ACCOUNT' does not look like an IB account ID (DU1234567 or U1234567)"
  if grep -q '^IB_ACCOUNT_ID=' .env; then
    sed -i "s/^IB_ACCOUNT_ID=.*/IB_ACCOUNT_ID=$ACCOUNT/" .env
  else
    echo "IB_ACCOUNT_ID=$ACCOUNT" >> .env
  fi
  export IB_ACCOUNT_ID="$ACCOUNT"
  echo "Saved IB_ACCOUNT_ID to .env"
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
  echo "execsys is already running: restarting it ..."
  "$ROOT/scripts/stop.sh" || die "could not stop the running session cleanly; check it with: tmux attach -t $SESSION"
fi

echo "Starting execsys ($MODE, from .env) on $IB_HOST:$IB_PORT, client_id $IB_CLIENT_ID"
echo "Strategies: $STRATEGIES"

ACTIVATE="cd '$ROOT' && source .venv/bin/activate"
# run.py loads .env itself: an already-running tmux server does not pass this script's environment on
tmux new-session -d -s "$SESSION" -n app "$ACTIVATE && RUN_MODE=$MODE python run.py $APP_ARGS; echo; echo 'execsys app exited (press Enter)'; read -r"
tmux set-option -t "$SESSION" status off          # no tmux bar: the dashboard is the only thing on screen
tmux set-option -t "$SESSION" remain-on-exit off
sleep 3  # give the app a moment to write its first snapshot
tmux new-window -t "$SESSION" -n dashboard "$ACTIVATE && python -m execsys.dashboard"
tmux select-window -t "$SESSION:dashboard"

if [[ -t 1 ]]; then
  exec tmux attach -t "$SESSION"
else
  echo "Running in tmux session '$SESSION' (attach with: tmux attach -t $SESSION)"
fi
