#!/usr/bin/env bash
# Stop the execsys tmux session cleanly: Ctrl+C to the app (cancels working orders, flushes state),
# wait for it to exit, then close the session.
set -euo pipefail

SESSION="execsys"
if ! tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "execsys is not running"
  exit 0
fi

echo "Stopping execsys.app (graceful) ..."
tmux send-keys -t "$SESSION:main.0" C-c
for _ in $(seq 1 30); do
  if ! pgrep -f "python -m execsys.app" >/dev/null; then
    break
  fi
  sleep 1
done
if pgrep -f "python -m execsys.app" >/dev/null; then
  echo "WARNING: execsys.app still running after 30 s; check the session: tmux attach -t $SESSION" >&2
  exit 1
fi
tmux kill-session -t "$SESSION"
echo "Stopped."
