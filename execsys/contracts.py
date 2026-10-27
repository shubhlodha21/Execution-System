"""
Message contracts shared by every module.

Modules never import each other; they only agree on the topics and message types defined here.
Messages travel over the Nautilus message bus with `publish_message` / `subscribe_topic`, which
passes the Python object by reference, so every message is a frozen dataclass.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


# Topics (publish on a concrete topic; subscribe with wildcards)
TOPIC_SIGNAL = "app.signal"  # app.signal.<strategy_id>
TOPIC_SIGNAL_ALL = "app.signal.*"
TOPIC_RISK_STATE = "app.risk.state"
TOPIC_ALERT = "app.alert"
TOPIC_ORDER_ISSUE = "app.order.issue"


def signal_topic(strategy_id: str) -> str:
    return f"{TOPIC_SIGNAL}.{strategy_id}"


@dataclass(frozen=True, slots=True)
class Signal:
    """
    A strategy's desired direction for one instrument.

    `direction` is the target exposure: +1 long, -1 short, 0 flat. The Order Manager turns
    it into a target position using that strategy's configured size, so publishing the
    same signal twice never creates a second order.
    """

    strategy_id: str
    instrument_id: str
    direction: int
    strength: float
    reason: str
    ts_event: int  # ns, time of the market data that caused the signal
    ts_init: int  # ns, time the signal was created

    def __post_init__(self) -> None:
        if self.direction not in (-1, 0, 1):
            raise ValueError(f"direction must be -1, 0 or 1, was {self.direction}")


class RiskState(enum.StrEnum):
    ACTIVE = "ACTIVE"  # normal trading
    REDUCING = "REDUCING"  # only orders that reduce existing positions
    HALTED = "HALTED"  # no new orders


@dataclass(frozen=True, slots=True)
class RiskStateChanged:
    state: RiskState
    reasons: tuple[str, ...]
    flatten: bool
    ts: int


@dataclass(frozen=True, slots=True)
class OrderIssue:
    """
    Published by the Order Manager when an order is denied (Risk Engine) or rejected (IB).
    """

    kind: str  # "denied" | "rejected"
    instrument_id: str
    reason: str
    ts: int


class Severity(enum.StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True, slots=True)
class Alert:
    severity: Severity
    module: str
    message: str
    ts: int

    @property
    def dedupe_key(self) -> str:
        return f"{self.severity}:{self.module}:{self.message}"
