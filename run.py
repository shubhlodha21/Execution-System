"""
Run ALL enabled strategies (config/settings.yaml) in one process.

    python run.py                 # paper/live trading per settings (needs IB_ACCOUNT_ID)
    python run.py --data-only     # signals only, no orders

Loads secrets such as IB_ACCOUNT_ID from .env first (values already in the environment win),
then starts execsys.app. Stop with Ctrl+C. Watch it with: python -m execsys.dashboard
"""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: Path) -> None:
    """
    Minimal KEY=VALUE reader for .env; comments and blank lines are ignored.
    """
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    os.chdir(root)  # settings, logs and runtime paths are relative to the project root
    load_dotenv(root / ".env")

    from execsys.app import main

    main()
