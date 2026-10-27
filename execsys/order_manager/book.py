"""
Pure order-management logic (no Nautilus imports, fully unit-tested).

- SignalBook: latest target per (instrument, strategy) -> net target per instrument.
- VirtualBook: splits real fills back to strategies for per-strategy position and PnL.
- plan_order: target vs. position vs. working orders vs. risk state -> order quantity.
"""

from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from execsys.contracts import RiskState
from execsys.contracts import Signal


ZERO = Decimal(0)


def _sign(x: Decimal) -> int:
    return (x > 0) - (x < 0)


@dataclass
class StrategyTarget:
    direction: int
    size: Decimal
    ts_event: int

    @property
    def quantity(self) -> Decimal:
        return self.size * self.direction


class SignalBook:
    def __init__(self) -> None:
        self._targets: dict[str, dict[str, StrategyTarget]] = {}

    def update(self, signal: Signal, size: Decimal) -> bool:
        """
        Store the signal. Returns False when it is older than the one already held
        (out-of-order delivery), so it changes nothing.
        """
        per_instrument = self._targets.setdefault(signal.instrument_id, {})
        current = per_instrument.get(signal.strategy_id)
        if current is not None and signal.ts_event < current.ts_event:
            return False
        per_instrument[signal.strategy_id] = StrategyTarget(signal.direction, size, signal.ts_event)
        return True

    def target(self, instrument_id: str) -> Decimal:
        return sum((t.quantity for t in self._targets.get(instrument_id, {}).values()), ZERO)

    def strategy_targets(self, instrument_id: str) -> dict[str, Decimal]:
        return {s: t.quantity for s, t in self._targets.get(instrument_id, {}).items()}

    def instruments(self) -> list[str]:
        return list(self._targets)

    def to_dict(self) -> dict[str, Any]:
        return {
            iid: {sid: {**asdict(t), "size": str(t.size)} for sid, t in per.items()}
            for iid, per in self._targets.items()
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SignalBook:
        book = cls()
        for iid, per in data.items():
            book._targets[iid] = {
                sid: StrategyTarget(int(t["direction"]), Decimal(t["size"]), int(t["ts_event"]))
                for sid, t in per.items()
            }
        return book


@dataclass
class VirtualPosition:
    quantity: Decimal = ZERO
    avg_px: Decimal = ZERO
    realized_pnl: Decimal = ZERO

    def apply(self, qty: Decimal, px: Decimal, multiplier: Decimal) -> None:
        """
        Average-cost accounting for a signed fill.
        """
        if qty == 0:
            return
        if self.quantity == 0 or _sign(self.quantity) == _sign(qty):
            new_qty = self.quantity + qty
            self.avg_px = (self.avg_px * abs(self.quantity) + px * abs(qty)) / abs(new_qty)
            self.quantity = new_qty
            return
        closing = min(abs(qty), abs(self.quantity))
        self.realized_pnl += (px - self.avg_px) * closing * _sign(self.quantity) * multiplier
        new_qty = self.quantity + qty
        if new_qty == 0:
            self.avg_px = ZERO
        elif _sign(new_qty) != _sign(self.quantity):
            self.avg_px = px  # flipped: the remainder opened at the fill price
        self.quantity = new_qty

    def unrealized_pnl(self, mark: Decimal, multiplier: Decimal) -> Decimal:
        return (mark - self.avg_px) * self.quantity * multiplier if self.quantity else ZERO


UNATTRIBUTED = "_UNATTRIBUTED"


class VirtualBook:
    def __init__(self) -> None:
        self._pos: dict[str, dict[str, VirtualPosition]] = {}

    def position(self, instrument_id: str, strategy_id: str) -> VirtualPosition:
        return self._pos.setdefault(instrument_id, {}).setdefault(strategy_id, VirtualPosition())

    def positions(self, instrument_id: str) -> dict[str, VirtualPosition]:
        return dict(self._pos.get(instrument_id, {}))

    def allocate_fill(
        self,
        instrument_id: str,
        fill_qty: Decimal,
        fill_px: Decimal,
        targets: dict[str, Decimal],
        multiplier: Decimal = Decimal(1),
    ) -> dict[str, Decimal]:
        """
        Split a signed fill across strategies whose virtual position is short of their target
        in the fill's direction, pro rata to that gap. Anything left goes to UNATTRIBUTED.
        Returns the allocation per strategy.
        """
        gaps = {}
        for sid, target in targets.items():
            gap = target - self.position(instrument_id, sid).quantity
            if gap != 0 and _sign(gap) == _sign(fill_qty):
                gaps[sid] = abs(gap)

        allocation: dict[str, Decimal] = {}
        remaining = abs(fill_qty)
        total_gap = sum(gaps.values(), ZERO)
        if total_gap > 0:
            to_split = min(remaining, total_gap)
            items = sorted(gaps.items())
            for i, (sid, gap) in enumerate(items):
                # Last strategy takes the rounding remainder so the split sums exactly
                share = to_split - sum(allocation.values(), ZERO) if i == len(items) - 1 else to_split * gap / total_gap
                allocation[sid] = share
            remaining -= to_split
        if remaining > 0:
            allocation[UNATTRIBUTED] = allocation.get(UNATTRIBUTED, ZERO) + remaining

        sign = Decimal(_sign(fill_qty))
        for sid, qty in allocation.items():
            self.position(instrument_id, sid).apply(qty * sign, fill_px, multiplier)
        return {sid: qty * sign for sid, qty in allocation.items()}

    def to_dict(self) -> dict[str, Any]:
        return {
            iid: {sid: {k: str(v) for k, v in asdict(p).items()} for sid, p in per.items()}
            for iid, per in self._pos.items()
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VirtualBook:
        book = cls()
        for iid, per in data.items():
            book._pos[iid] = {
                sid: VirtualPosition(Decimal(p["quantity"]), Decimal(p["avg_px"]), Decimal(p["realized_pnl"]))
                for sid, p in per.items()
            }
        return book


@dataclass(frozen=True)
class OrderPlan:
    quantity: Decimal  # signed; 0 means no order
    reason: str


def plan_order(
    target: Decimal,
    position: Decimal,
    working: Decimal,
    max_position: Decimal,
    risk_state: RiskState,
) -> OrderPlan:
    """
    Decide the signed order quantity that moves `position` toward `target`.

    `working` is the signed remaining quantity of open orders. While any order is working
    for the instrument nothing new is sent: the next evaluation happens when it completes,
    which prevents duplicate and stacked orders.
    """
    desired = max(-max_position, min(max_position, target))
    if risk_state is RiskState.HALTED:
        return OrderPlan(ZERO, "halted")
    if risk_state is RiskState.REDUCING:
        # Only move toward flat, never past it
        if position > 0:
            desired = max(ZERO, min(position, desired))
        elif position < 0:
            desired = min(ZERO, max(position, desired))
        else:
            desired = ZERO
    if working != 0:
        return OrderPlan(ZERO, "working order pending")
    delta = desired - position
    if delta == 0:
        return OrderPlan(ZERO, "at target")
    return OrderPlan(delta, "rebalance")
