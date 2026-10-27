"""
State Store: keeps module state that must survive a restart (Order Manager targets, virtual
positions). Saves are coalesced and written atomically on a background thread, so callers on
the event thread never touch the disk.

File-based for now; swap for Redis/Postgres behind the same `load` / `save` interface.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any


_log = logging.getLogger("execsys.state_store")


class JsonStateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._pending: dict[str, Any] | None = None
        self._cond = threading.Condition()
        self._stopping = False
        self._thread: threading.Thread | None = None

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _log.exception("state file %s unreadable; starting empty", self.path)
            return {}

    def start(self) -> None:
        if self._thread is None:
            self._stopping = False
            self._thread = threading.Thread(target=self._run, name=f"state-store-{self.path.stem}", daemon=True)
            self._thread.start()

    def save(self, state: dict[str, Any]) -> None:
        """
        Non-blocking: replaces any not-yet-written snapshot with this one.
        `state` must not be mutated afterwards (pass a fresh dict).
        """
        with self._cond:
            self._pending = state
            self._cond.notify()

    def stop(self) -> None:
        with self._cond:
            self._stopping = True
            self._cond.notify()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self) -> None:
        while True:
            with self._cond:
                while self._pending is None and not self._stopping:
                    self._cond.wait()
                state, self._pending = self._pending, None
                stopping = self._stopping
            if state is not None:
                self._write(state)
            if stopping:
                return

    def _write(self, state: dict[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(state, indent=1, default=str), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            _log.exception("failed to write state file %s", self.path)
