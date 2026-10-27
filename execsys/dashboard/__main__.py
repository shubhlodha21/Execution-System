"""
Terminal dashboard for the running execution system.

    python -m execsys.dashboard                 # full-screen, refresh every second
    python -m execsys.dashboard --once          # print one screen and exit
    python -m execsys.dashboard --no-color

Keys: 1 all · 2 tickers · 3 strategies · 4 signals & orders · p pause · q quit.

Reads the snapshot the app writes (dashboard.status_file) plus the Order Manager state file. It opens
no IB connection, so it runs next to the app and needs no client_id.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from execsys.config import load_settings
from execsys.dashboard.render import VIEWS
from execsys.dashboard.render import LtpTracker
from execsys.dashboard.render import build


_ENTER = "\x1b[?1049h\x1b[?25l"  # alternate screen, hide cursor
_LEAVE = "\x1b[?25h\x1b[?1049l"
_HOME_CLEAR = "\x1b[H\x1b[2J"


def read_json(path: str | Path) -> dict[str, Any] | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


class KeyReader:
    """
    Single key presses without Enter (Linux/macOS via termios, Windows via msvcrt).
    Restores the terminal on exit; without a TTY it just waits.
    """

    def __init__(self) -> None:
        self._fd: int | None = None
        self._saved: Any = None

    def __enter__(self) -> KeyReader:
        if os.name != "nt" and sys.stdin.isatty():
            import termios
            import tty

            self._fd = sys.stdin.fileno()
            self._saved = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._fd is not None:
            import termios

            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)

    def get(self, timeout: float) -> str | None:
        if os.name == "nt":
            import msvcrt

            end = time.monotonic() + timeout
            while time.monotonic() < end:
                if msvcrt.kbhit():
                    return msvcrt.getwch()
                time.sleep(0.05)
            return None
        if self._fd is None:
            time.sleep(timeout)
            return None
        import select

        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        return sys.stdin.read(1) if ready else None


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m execsys.dashboard", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--refresh", type=float, default=1.0, help="seconds between refreshes")
    parser.add_argument("--view", choices=sorted(set(VIEWS.values())), default="all")
    parser.add_argument("--once", action="store_true", help="print one screen and exit")
    parser.add_argument("--no-color", action="store_true")
    args = parser.parse_args()
    settings = load_settings(args.config)
    if os.name == "nt":
        os.system("")  # enable ANSI colours in the Windows console
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # box lines and arrows on non-UTF-8 consoles

    tracker = LtpTracker()
    view, paused = args.view, False
    status: dict[str, Any] | None = None
    om_state: dict[str, Any] | None = None

    def screen() -> str:
        return build(status, om_state, time.time_ns(), view, tracker, paused, color=not args.no_color)

    def refresh() -> None:
        nonlocal status, om_state
        status = read_json(settings.dashboard.status_file)
        om_state = read_json(settings.order_manager.state_file)
        if status:
            tracker.update(status["tickers"])

    if args.once:
        refresh()
        print(screen())
        return

    sys.stdout.write(_ENTER)
    try:
        with KeyReader() as keys:
            while True:
                if not paused:
                    refresh()
                sys.stdout.write(_HOME_CLEAR + screen())
                sys.stdout.flush()
                key = keys.get(args.refresh)
                if key is None:
                    continue
                key = key.lower()
                if key in ("q", "\x1b", "\x03"):
                    break
                if key == "p":
                    paused = not paused
                elif key in VIEWS:
                    view = VIEWS[key]
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write(_LEAVE)
        sys.stdout.flush()


if __name__ == "__main__":
    main()
