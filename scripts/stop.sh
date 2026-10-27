#!/usr/bin/env bash
# Stop the execsys tmux session cleanly: Ctrl+C to every pane (the app cancels working orders and
# flushes state), wait for the strategies to exit, then close the session.
set -euo pipefail

SESSION="execsys"
APP_PATTERN="python (run\.py|-m execsys\.app)"

if ! tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "execsys is not running"
  exit 0
fi

echo "Stopping the strategies (graceful) ..."
for pane in $(tmux list-panes -s -t "$SESSION" -F '#{pane_id}'); do
  tmux send-keys -t "$pane" C-c || true
done
for _ in $(seq 1 30); do
  if ! pgrep -f "$APP_PATTERN" >/dev/null; then
    break
  fi
  sleep 1
done
if pgrep -f "$APP_PATTERN" >/dev/null; then
  echo "WARNING: the strategies are still running after 30 s; check the session: tmux attach -t $SESSION" >&2
  exit 1
fi
tmux kill-session -t "$SESSION"
echo "Stopped."
