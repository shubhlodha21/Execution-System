"""
Operator control CLI. Writes a command file the Risk Monitor picks up within one check interval.

    python -m execsys.ops halt      # no new orders (latches)
    python -m execsys.ops reduce    # only position-reducing orders
    python -m execsys.ops flatten   # halt and close all positions
    python -m execsys.ops resume    # clear manual state and latched halt
    python -m execsys.ops status    # print saved risk / order manager state
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from execsys.config import load_settings


COMMANDS = ("halt", "reduce", "flatten", "resume")


def write_command(control_file: str | Path, command: str) -> None:
    path = Path(control_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"command": command, "ts": time.time()}), encoding="utf-8")
    os.replace(tmp, path)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m execsys.ops")
    parser.add_argument("command", choices=(*COMMANDS, "status"))
    parser.add_argument("--config", default="config/settings.yaml")
    args = parser.parse_args()
    settings = load_settings(args.config)

    if args.command == "status":
        for f in (settings.ops.risk_state_file, settings.order_manager.state_file, settings.ops.control_file):
            p = Path(f)
            print(f"== {f}")
            print(p.read_text(encoding="utf-8") if p.exists() else "(missing)")
        return

    write_command(settings.ops.control_file, args.command)
    print(f"Sent '{args.command}'. The Risk Monitor applies it within {settings.risk_limits.check_interval_ms} ms.")


if __name__ == "__main__":
    main()
