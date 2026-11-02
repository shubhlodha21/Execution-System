"""
Pure trade-journal accounting (no Nautilus imports, unit-tested).

Average-cost P&L per ticker from IB executions: each fill updates the ticker's position and average
cost, and realises P&L on the part that closes or reduces the position. Daily statistics roll up per
(UTC day, ticker). Positions carry across days; daily figures start from zero each day.
"""

from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from decimal import Decimal
from typing import Any


ZERO = Decimal(0)

FILL_FIELDS = (
    "time_utc", "time_ist", "ticker", "side", "quantity", "price", "commission", "commission_ccy",
    "position_after", "avg_cost_after", "realized_gross", "realized_net", "day_realized_net",
    "strategies", "ib_execution_id", "ib_order_id", "client_order_id",
)
SUMMARY_FIELDS = (
    "date", "ticker", "trades", "bought_qty", "sold_qty", "avg_buy_price", "avg_sell_price",
    "realized_gross", "commission", "realized_net", "end_position", "end_avg_cost",
)


def _sign(x: Decimal) -> int:
    return (x > 0) - (x < 0)


@dataclass
class TickerPosition:
    quantity: Decimal = ZERO
    avg_cost: Decimal = ZERO

    def apply(self, signed_qty: Decimal, price: Decimal, multiplier: Decimal) -> Decimal:
        """
        Apply a signed fill; return the gross P&L it realises (before commission).
        """
        if signed_qty == 0:
            return ZERO
        if self.quantity == 0 or _sign(self.quantity) == _sign(signed_qty):
            new_qty = self.quantity + signed_qty
            self.avg_cost = (self.avg_cost * abs(self.quantity) + price * abs(signed_qty)) / abs(new_qty)
            self.quantity = new_qty
            return ZERO
        closing = min(abs(signed_qty), abs(self.quantity))
        realized = (price - self.avg_cost) * closing * _sign(self.quantity) * multiplier
        new_qty = self.quantity + signed_qty
        if new_qty == 0:
            self.avg_cost = ZERO
        elif _sign(new_qty) != _sign(self.quantity):
            self.avg_cost = price  # flipped: the remainder opened at this fill's price
        self.quantity = new_qty
        return realized


@dataclass
class DayStats:
    trades: int = 0
    bought_qty: Decimal = ZERO
    sold_qty: Decimal = ZERO
    bought_value: Decimal = ZERO
    sold_value: Decimal = ZERO
    realized_gross: Decimal = ZERO
    commission: Decimal = ZERO

    @property
    def realized_net(self) -> Decimal:
        return self.realized_gross - self.commission


@dataclass
class FillRecord:
    realized_gross: Decimal
    realized_net: Decimal
    day_realized_net: Decimal
    position_after: Decimal
    avg_cost_after: Decimal


@dataclass
class Ledger:
    positions: dict[str, TickerPosition] = field(default_factory=dict)
    days: dict[str, dict[str, DayStats]] = field(default_factory=dict)  # date -> ticker -> stats

    def seed(self, ticker: str, quantity: Decimal, avg_cost: Decimal) -> None:
        """
        Set a ticker's position from IB (reconciled at startup); IB is the source of truth.
        """
        self.positions[ticker] = TickerPosition(quantity, avg_cost if quantity != 0 else ZERO)

    def apply_fill(
        self,
        day: str,
        ticker: str,
        side: str,
        quantity: Decimal,
        price: Decimal,
        commission: Decimal,
        multiplier: Decimal = Decimal(1),
        commission_in_quote_ccy: bool = True,
    ) -> FillRecord:
        signed = quantity if side == "BUY" else -quantity
        pos = self.positions.setdefault(ticker, TickerPosition())
        gross = pos.apply(signed, price, multiplier)
        cost = commission if commission_in_quote_ccy else ZERO

        stats = self.days.setdefault(day, {}).setdefault(ticker, DayStats())
        stats.trades += 1
        if side == "BUY":
            stats.bought_qty += quantity
            stats.bought_value += quantity * price
        else:
            stats.sold_qty += quantity
            stats.sold_value += quantity * price
        stats.realized_gross += gross
        stats.commission += cost
        return FillRecord(gross, gross - cost, stats.realized_net, pos.quantity, pos.avg_cost)

    def summary_rows(self, day: str) -> list[tuple[str, ...]]:
        rows = []
        for ticker, s in sorted(self.days.get(day, {}).items()):
            pos = self.positions.get(ticker, TickerPosition())
            avg_buy = s.bought_value / s.bought_qty if s.bought_qty else None
            avg_sell = s.sold_value / s.sold_qty if s.sold_qty else None
            rows.append(
                (
                    day, ticker, str(s.trades), str(s.bought_qty), str(s.sold_qty),
                    "" if avg_buy is None else f"{avg_buy:.6f}", "" if avg_sell is None else f"{avg_sell:.6f}",
                    f"{s.realized_gross:.2f}", f"{s.commission:.2f}", f"{s.realized_net:.2f}",
                    str(pos.quantity), f"{pos.avg_cost:.6f}",
                ),
            )
        return rows

    # -- persistence (JSON-safe, Decimal as strings) ---------------------------------------

    def to_dict(self, keep_days: set[str]) -> dict[str, Any]:
        return {
            "positions": {t: {k: str(v) for k, v in asdict(p).items()} for t, p in self.positions.items()},
            "days": {
                d: {t: {k: str(v) for k, v in asdict(s).items()} for t, s in per.items()}
                for d, per in self.days.items()
                if d in keep_days
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Ledger:
        ledger = cls()
        for t, p in data.get("positions", {}).items():
            ledger.positions[t] = TickerPosition(Decimal(p["quantity"]), Decimal(p["avg_cost"]))
        for d, per in data.get("days", {}).items():
            ledger.days[d] = {
                t: DayStats(
                    int(s["trades"]), Decimal(s["bought_qty"]), Decimal(s["sold_qty"]), Decimal(s["bought_value"]),
                    Decimal(s["sold_value"]), Decimal(s["realized_gross"]), Decimal(s["commission"]),
                )
                for t, s in per.items()
            }
        return ledger
