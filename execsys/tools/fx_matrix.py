"""
Pure FX maths and text rendering for the FX monitor (no Nautilus imports, unit-tested).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass


# India Standard Time has no daylight saving, so a fixed offset is exact and needs no tz database
IST = dt.timezone(dt.timedelta(hours=5, minutes=30), "IST")


@dataclass(frozen=True)
class FxQuote:
    bid: float
    ask: float
    ts_ns: int

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2


def split_pair(instrument_id: str) -> tuple[str, str]:
    """
    "EUR/USD.IDEALPRO" -> ("EUR", "USD")
    """
    symbol = instrument_id.split(".", 1)[0]
    base, quote = symbol.split("/")
    return base, quote


def pip_size(quote_ccy: str) -> float:
    return 0.01 if quote_ccy == "JPY" else 0.0001


def cross_matrix(mids: dict[tuple[str, str], float], currencies: list[str]) -> dict[tuple[str, str], float | None]:
    """
    Rate for every (from, to) currency pair: how many `to` units one `from` unit buys.
    Uses direct quotes, their inverse, or one intermediate currency (e.g. EUR->JPY via USD).
    """

    def direct(a: str, b: str) -> float | None:
        if a == b:
            return 1.0
        if (a, b) in mids:
            return mids[(a, b)]
        if (b, a) in mids and mids[(b, a)]:
            return 1 / mids[(b, a)]
        return None

    out: dict[tuple[str, str], float | None] = {}
    for a in currencies:
        for b in currencies:
            rate = direct(a, b)
            if rate is None:
                for c in currencies:
                    ac, cb = direct(a, c), direct(c, b)
                    if c not in (a, b) and ac is not None and cb is not None:
                        rate = ac * cb
                        break
            out[(a, b)] = rate
    return out


def _fmt(x: float | None) -> str:
    if x is None:
        return "-"
    return f"{x:,.3f}" if x >= 100 else f"{x:.5f}"


def render(quotes: dict[str, FxQuote], pairs: list[str], now_ns: int) -> str:
    """
    Quote table plus cross-rate matrix as plain text.
    """
    now = dt.datetime.fromtimestamp(now_ns / 1e9, dt.UTC)
    ist = now.astimezone(IST)
    lines = [f"FX monitor  {now:%Y-%m-%d %H:%M:%S} UTC  |  {ist:%Y-%m-%d %H:%M:%S} IST  (IB IDEALPRO, Ctrl+C to quit)", ""]
    lines.append(f"{'Pair':<10}{'Bid':>12}{'Ask':>12}{'Mid':>12}{'Spread':>9}{'Age':>8}")
    lines.append("-" * 63)
    mids: dict[tuple[str, str], float] = {}
    currencies: list[str] = []
    for iid in pairs:
        base, quote = split_pair(iid)
        for c in (base, quote):
            if c not in currencies:
                currencies.append(c)
        q = quotes.get(iid)
        if q is None:
            lines.append(f"{base + '/' + quote:<10}{'waiting for quotes ...':>53}")
            continue
        mids[(base, quote)] = q.mid
        spread_pips = (q.ask - q.bid) / pip_size(quote)
        age = (now_ns - q.ts_ns) / 1e9
        lines.append(
            f"{base + '/' + quote:<10}{_fmt(q.bid):>12}{_fmt(q.ask):>12}{_fmt(q.mid):>12}"
            f"{spread_pips:>7.1f}p{age:>7.1f}s",
        )

    lines += ["", "Cross rates (1 row currency = X column currency, from mids)", ""]
    lines.append(f"{'':<6}" + "".join(f"{c:>12}" for c in currencies))
    matrix = cross_matrix(mids, currencies)
    for a in currencies:
        lines.append(f"{a:<6}" + "".join(f"{_fmt(matrix[(a, b)]):>12}" for b in currencies))
    return "\n".join(lines)
