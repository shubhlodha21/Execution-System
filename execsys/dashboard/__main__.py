"""
Terminal dashboard for the running execution system.

    python -m execsys.dashboard                 # refresh every second
    python -m execsys.dashboard --once          # print one screen and exit
    python -m execsys.dashboard --no-color

Reads the snapshot the app writes (dashboard.status_file) plus the Order Manager state file. It opens
no IB connection, so it works next to the running app and does not need a client_id.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from execsys.config import load_settings
from execsys.dashboard.render import render


_CLEAR = "\x1b[H\x1b[2J"


def read_json(path: str | Path) -> dict[str, Any] | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m execsys.dashboard", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--refresh", type=float, default=1.0, help="seconds between refreshes")
    parser.add_argument("--once", action="store_true", help="print one screen and exit")
    parser.add_argument("--no-color", action="store_true")
    args = parser.parse_args()
    settings = load_settings(args.config)

    def screen() -> str:
        return render(
            read_json(settings.dashboard.status_file),
            read_json(settings.order_manager.state_file),
            time.time_ns(),
            color=not args.no_color,
        )

    if args.once:
        print(screen())
        return
    try:
        while True:
            print(_CLEAR + screen(), flush=True)
            time.sleep(args.refresh)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
