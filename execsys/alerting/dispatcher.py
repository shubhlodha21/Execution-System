"""
Alert dispatcher: de-duplicates and rate-limits alerts, then delivers them from a worker thread
(log file always, Telegram when TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are set).
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from collections.abc import Callable

from execsys.config import AlertSettings
from execsys.contracts import Alert
from execsys.contracts import Severity


_log = logging.getLogger("execsys.alerts")

Channel = Callable[[Alert], None]


def log_channel(alert: Alert) -> None:
    level = {Severity.INFO: logging.INFO, Severity.WARNING: logging.WARNING}.get(alert.severity, logging.CRITICAL)
    _log.log(level, "[%s] %s: %s", alert.severity, alert.module, alert.message)


def telegram_channel(token: str, chat_id: str) -> Channel:
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    def send(alert: Alert) -> None:
        text = f"[{alert.severity}] {alert.module}\n{alert.message}"
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        with urllib.request.urlopen(url, data=data, timeout=10) as resp:  # noqa: S310 (fixed https URL)
            body = json.loads(resp.read())
            if not body.get("ok"):
                raise RuntimeError(f"telegram error: {body}")

    return send


class AlertFilter:
    """
    Pure de-duplication + rate limit (unit-tested). `allow` returns False for an alert
    seen within `dedupe_secs`, or when more than `max_per_minute` passed in the last minute.
    CRITICAL alerts bypass the rate limit but are still de-duplicated.
    """

    def __init__(self, dedupe_secs: float, max_per_minute: int) -> None:
        self.dedupe_secs = dedupe_secs
        self.max_per_minute = max_per_minute
        self._seen: dict[str, float] = {}
        self._sent: deque[float] = deque()

    def allow(self, alert: Alert, now: float) -> bool:
        last = self._seen.get(alert.dedupe_key)
        if last is not None and now - last < self.dedupe_secs:
            return False
        while self._sent and now - self._sent[0] > 60:
            self._sent.popleft()
        if alert.severity is not Severity.CRITICAL and len(self._sent) >= self.max_per_minute:
            return False
        self._seen[alert.dedupe_key] = now
        self._sent.append(now)
        if len(self._seen) > 10_000:
            cutoff = now - self.dedupe_secs
            self._seen = {k: t for k, t in self._seen.items() if t >= cutoff}
        return True


class AlertDispatcher:
    def __init__(self, settings: AlertSettings, channels: list[Channel] | None = None) -> None:
        self.filter = AlertFilter(settings.dedupe_secs, settings.max_per_minute)
        if channels is None:
            channels = [log_channel]
            if settings.telegram_bot_token and settings.telegram_chat_id:
                channels.append(telegram_channel(settings.telegram_bot_token, settings.telegram_chat_id))
        self.channels = channels
        self._q: queue.Queue[Alert | None] = queue.Queue(maxsize=1000)
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="alert-dispatcher", daemon=True)
            self._thread.start()

    def submit(self, alert: Alert) -> None:
        """
        Non-blocking; safe to call from the event thread.
        """
        try:
            self._q.put_nowait(alert)
        except queue.Full:
            _log.error("alert queue full; dropped: %s", alert)

    def stop(self) -> None:
        if self._thread is not None:
            self._q.put(None)
            self._thread.join(timeout=15)
            self._thread = None

    def _run(self) -> None:
        while (alert := self._q.get()) is not None:
            if not self.filter.allow(alert, time.monotonic()):
                continue
            for channel in self.channels:
                for attempt in range(3):
                    try:
                        channel(alert)
                        break
                    except Exception:
                        _log.exception("alert channel failed (attempt %d)", attempt + 1)
                        time.sleep(1 + attempt)
