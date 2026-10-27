#!/usr/bin/env bash
# Stop execsys gracefully without killing anything: Ctrl+C to every pane (the app cancels working
# orders and flushes state, the dashboard exits), then Enter to dismiss the app window's prompt.
# With nothing left running, tmux closes the session by itself.
set -euo pipefail

SESSION="execsys"
APP_PATTERN="python (run\.py|-m execsys\.app)"

if ! tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "execsys is not running"
  exit 0
fi

send_all() {
  for pane in $(tmux list-panes -s -t "$SESSION" -F '#{pane_id}' 2>/dev/null); do
    tmux send-keys -t "$pane" "$@" 2>/dev/null || true
  done
}

echo "Stopping the strategies (graceful) ..."
send_all C-c
for _ in $(seq 1 30); do
  pgrep -f "$APP_PATTERN" >/dev/null || break
  sleep 1
done
if pgrep -f "$APP_PATTERN" >/dev/null; then
  echo "WARNING: the strategies are still running after 30 s; check the session: tmux attach -t $SESSION" >&2
  exit 1
fi

# Dismiss the "press Enter" prompt left in the app window; the session then ends on its own
send_all Enter
for _ in $(seq 1 10); do
  tmux has-session -t "$SESSION" 2>/dev/null || { echo "Stopped."; exit 0; }
  sleep 1
done
echo "Strategies stopped, but the tmux session '$SESSION' is still open (something is running in it)." >&2
echo "Look at it with: tmux attach -t $SESSION" >&2
exit 1
