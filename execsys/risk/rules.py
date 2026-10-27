"""
Pure risk rules (no Nautilus imports, fully unit-tested).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from decimal import Decimal

from execsys.config import RiskLimits
from execsys.contracts import RiskState


_SEVERITY = {RiskState.ACTIVE: 0, RiskState.REDUCING: 1, RiskState.HALTED: 2}


def worst(a: RiskState, b: RiskState) -> RiskState:
    return a if _SEVERITY[a] >= _SEVERITY[b] else b


@dataclass(frozen=True)
class RiskSnapshot:
    daily_pnl: Decimal
    positions: dict[str, Decimal]
    gross_exposure: Decimal
    data_age_secs: dict[str, float | None]  # None = no data received yet
    rejects_in_window: int
    manual: RiskState | None = None


@dataclass
class RiskDecision:
    state: RiskState = RiskState.ACTIVE
    reasons: list[str] = field(default_factory=list)

    def raise_to(self, state: RiskState, reason: str) -> None:
        self.state = worst(self.state, state)
        self.reasons.append(reason)


def evaluate(snap: RiskSnapshot, limits: RiskLimits, max_position: Callable[[str], Decimal]) -> RiskDecision:
    d = RiskDecision()
    if snap.manual is not None and snap.manual is not RiskState.ACTIVE:
        d.raise_to(snap.manual, f"manual {snap.manual}")
    if snap.daily_pnl <= -limits.max_daily_loss:
        d.raise_to(RiskState.HALTED, f"daily loss {snap.daily_pnl} <= -{limits.max_daily_loss}")
    if snap.rejects_in_window >= limits.max_rejects_per_window:
        d.raise_to(
            RiskState.HALTED,
            f"{snap.rejects_in_window} order rejects/denials in {limits.reject_window_secs}s",
        )
    for iid, qty in snap.positions.items():
        limit = max_position(iid)
        if abs(qty) > limit:
            d.raise_to(RiskState.REDUCING, f"{iid} position {qty} exceeds {limit}")
    if snap.gross_exposure > limits.max_gross_exposure:
        d.raise_to(RiskState.REDUCING, f"gross exposure {snap.gross_exposure} > {limits.max_gross_exposure}")
    for iid, age in snap.data_age_secs.items():
        # Stale data only matters where we hold risk
        if snap.positions.get(iid, Decimal(0)) != 0 and (age is None or age > limits.stale_data_secs):
            detail = "no data received" if age is None else f"last update {round(age)}s ago"
            d.raise_to(RiskState.REDUCING, f"{iid} market data stale ({detail})")
    return d
