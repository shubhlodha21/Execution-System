"""
Pure text rendering of a status snapshot for the terminal dashboard (no Nautilus imports, unit-tested).

Plain ANSI colours and box drawing, no extra libraries. `build()` returns the whole screen as a string
for the selected view; `LtpTracker` remembers each ticker's last move so LTP shows a coloured arrow.
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from decimal import InvalidOperation
from typing import Any

from execsys.tools.fx_matrix import IST


STALE_SECS = 5.0
VIEWS = {"1": "all", "2": "tickers", "3": "strategies", "4": "activity"}
VIEW_LABELS = {"all": "All", "tickers": "Tickers", "strategies": "Strategies", "activity": "Signals & orders"}

RESET, BOLD, DIM, REVERSE = "\x1b[0m", "\x1b[1m", "\x1b[2m", "\x1b[7m"
GREEN, RED, YELLOW, CYAN, MAGENTA, WHITE = "\x1b[32m", "\x1b[31m", "\x1b[33m", "\x1b[36m", "\x1b[35m", "\x1b[97m"
ON_GREEN, ON_RED, ON_YELLOW, ON_BLUE = "\x1b[30;42m", "\x1b[97;41m", "\x1b[30;43m", "\x1b[97;44m"
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


class Paint:
    def __init__(self, color: bool) -> None:
        self.on = color

    def __call__(self, text: str, *codes: str) -> str:
        return f"{''.join(codes)}{text}{RESET}" if self.on and codes else text

    def badge(self, text: str, code: str) -> str:
        return self(f" {text} ", code, BOLD) if self.on else f"[{text}]"


class LtpTracker:
    """
    Remembers the last LTP per ticker and the direction of its last change (+1 up, -1 down).
    """

    def __init__(self) -> None:
        self.last: dict[str, Decimal] = {}
        self.moves: dict[str, int] = {}

    def update(self, tickers: list[dict[str, Any]]) -> None:
        for t in tickers:
            value = _dec(t.get("ltp"))
            if value is None:
                continue
            prev = self.last.get(t["id"])
            if prev is not None and value != prev:
                self.moves[t["id"]] = 1 if value > prev else -1
            self.last[t["id"]] = value

    def move(self, ticker: str) -> int:
        return self.moves.get(ticker, 0)


# -- formatting helpers ---------------------------------------------------------------------


def _dec(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def vlen(text: str) -> int:
    return len(_ANSI.sub("", text))


def clip(line: str, width: int) -> str:
    """
    Cut a line to `width` visible characters, keeping colour codes intact, so narrow terminals
    show a clean right edge instead of wrapped table rows.
    """
    if vlen(line) <= width:
        return line
    out, seen, i = [], 0, 0
    while i < len(line) and seen < width - 1:
        m = _ANSI.match(line, i)
        if m:
            out.append(m.group())
            i = m.end()
            continue
        out.append(line[i])
        seen += 1
        i += 1
    return "".join(out) + RESET + "…"


def _pad(text: str, width: int, align: str) -> str:
    gap = " " * max(0, width - vlen(text))
    return gap + text if align == "r" else text + gap


def price(value: Any) -> str:
    d = _dec(value)
    if d is None:
        return "-"
    if abs(d) >= 1000:
        return f"{d:,.2f}"
    if abs(d) >= 100:
        return f"{d:,.3f}"
    return f"{d:.5f}"


def qty(value: Any) -> str:
    d = _dec(value)
    if d is None:
        return "-"
    return f"{d:,.0f}" if d == d.to_integral() else f"{d:,f}"


def ago(now_ns: int, ns: int) -> str:
    if not ns:
        return "-"
    secs = max(0.0, (now_ns - ns) / 1e9)
    if secs < 60:
        return f"{secs:.0f}s ago"
    if secs < 3600:
        return f"{secs / 60:.0f}m ago"
    if secs < 86400:
        return f"{secs / 3600:.1f}h ago"
    return f"{secs / 86400:.1f}d ago"


def ist_time(ns: int) -> str:
    if not ns:
        return "-"
    return dt.datetime.fromtimestamp(ns / 1e9, dt.UTC).astimezone(IST).strftime("%H:%M:%S")


def uptime(secs: float) -> str:
    h, rem = divmod(int(max(0, secs)), 3600)
    return f"{h}h {rem // 60:02d}m"


def human_timeframe(spec: str) -> str:
    """
    "5-MINUTE-MID-EXTERNAL" -> "5 min · mid"
    """
    parts = spec.split("-")
    if len(parts) < 3:
        return spec
    units = {"SECOND": "sec", "MINUTE": "min", "HOUR": "hour", "DAY": "day", "WEEK": "week", "MONTH": "month"}
    return f"{parts[0]} {units.get(parts[1], parts[1].lower())} · {parts[2].lower()}"


def human_strategy(cls: str) -> str:
    return {"EmaCrossSignal": "EMA crossover", "RsiSignal": "RSI overbought/oversold"}.get(cls, cls)


def human_params(params: str) -> str:
    names = {"fast_period": "fast", "slow_period": "slow", "period": "period", "overbought": "OB",
             "oversold": "OS", "exit_level": "exit"}
    out = []
    for item in filter(None, (s.strip() for s in params.split(","))):
        key, _, value = item.partition("=")
        out.append(f"{names.get(key, key)} {value}")
    return " · ".join(out)


def _signed(p: Paint, value: Any) -> str:
    d = _dec(value)
    if d is None:
        return p("-", DIM)
    text = f"{d:+,.2f}"
    return p(text, GREEN, BOLD) if d > 0 else p(text, RED, BOLD) if d < 0 else p(text, DIM)


def _position(p: Paint, value: Any) -> str:
    d = _dec(value)
    if d is None or d == 0:
        return p("flat", DIM)
    return p(qty(d), GREEN) if d > 0 else p(qty(d), RED)


def _signal(p: Paint, direction: int | None) -> str:
    if direction == 1:
        return p.badge("LONG", ON_GREEN)
    if direction == -1:
        return p.badge("SHORT", ON_RED)
    if direction == 0:
        return p(" FLAT ", DIM)
    return p("  -   ", DIM)


def _freshness(p: Paint, now_ns: int, ns: int) -> str:
    text = ago(now_ns, ns)
    if not ns:
        return p(text, DIM)
    secs = (now_ns - ns) / 1e9
    return p(text, GREEN) if secs < 5 else p(text, YELLOW) if secs < 60 else p(text, RED)


def table(p: Paint, headers: list[str], rows: list[list[str]], aligns: str) -> list[str]:
    widths = [max([vlen(h)] + [vlen(r[i]) for r in rows]) for i, h in enumerate(headers)]

    def line(left: str, mid: str, right: str) -> str:
        return p(left + mid.join("─" * (w + 2) for w in widths) + right, DIM)

    bar = p("│", DIM)
    out = [line("┌", "┬", "┐")]
    out.append(bar + bar.join(f" {_pad(p(h, BOLD, CYAN), w, a)} " for h, w, a in zip(headers, widths, aligns)) + bar)
    out.append(line("├", "┼", "┤"))
    for r in rows:
        out.append(bar + bar.join(f" {_pad(c, w, a)} " for c, w, a in zip(r, widths, aligns)) + bar)
    if not rows:
        out.append(bar + _pad(p(" nothing yet", DIM), sum(widths) + 3 * len(widths) - 1, "l") + bar)
    out.append(line("└", "┴", "┘"))
    return out


def _section(p: Paint, title: str, note: str = "") -> str:
    return p(f" {title} ", BOLD, MAGENTA) + (p(f" {note}", DIM) if note else "")


# -- screen ---------------------------------------------------------------------------------


def build(
    status: dict[str, Any] | None,
    om_state: dict[str, Any] | None,
    now_ns: int,
    view: str = "all",
    tracker: LtpTracker | None = None,
    paused: bool = False,
    color: bool = True,
) -> str:
    """
    The whole dashboard screen for `view` ("all", "tickers", "strategies", "activity").
    `status` is runtime/status.json and `om_state` the Order Manager state file.
    """
    p = Paint(color)
    now = dt.datetime.fromtimestamp(now_ns / 1e9, dt.UTC)
    clock = f"{now.astimezone(IST):%d %b %Y  %H:%M:%S} IST  ·  {now:%H:%M:%S} UTC"
    out: list[str] = []

    if not status:
        out += [
            p(" EXECSYS ", BOLD, WHITE) + "  " + p(clock, DIM),
            "",
            p("  Waiting for the trading app ...", YELLOW, BOLD),
            p("  Start it with:  python -m execsys.app   (dashboard.enabled: true in settings.yaml)", DIM),
        ]
        return "\n".join(out + ["", _footer(p, view, paused)])

    node, risk = status["node"], status["risk"]
    age_s = (now_ns - status["ts_ns"]) / 1e9
    if status.get("stopped"):
        app = p.badge("● STOPPED", ON_RED)
    elif age_s > STALE_SECS:
        app = p.badge(f"● NOT RESPONDING {age_s:.0f}s", ON_RED)
    else:
        app = p.badge("● RUNNING", ON_GREEN)
    risk_badge = p.badge(f"RISK {risk['state']}", {"ACTIVE": ON_GREEN, "REDUCING": ON_YELLOW}.get(risk["state"], ON_RED))
    mode = p.badge(node["mode"], ON_BLUE if node["mode"] == "PAPER" else ON_RED)
    data_only = "  " + p.badge("DATA ONLY", ON_YELLOW) if node.get("data_only") else ""
    conns = "  ".join(
        f"{k} " + (p("● connected", GREEN) if v == "CONNECTED" else p(f"● {v.lower()}", RED, BOLD))
        for k, v in status["connection"].items()
    )
    up = uptime((status["ts_ns"] - status["started_ns"]) / 1e9) if status.get("started_ns") else "-"

    title = " · ".join(dict.fromkeys(["EXECSYS DASHBOARD", node["name"], node["trader_id"]]))
    out.append(p(f" {title} ", BOLD, WHITE) + "   " + p(clock, DIM))
    out.append(f" {app}  {risk_badge}  {mode}{data_only}   {p('Uptime', DIM)} {up}   {p('Updated', DIM)} {ago(now_ns, status['ts_ns'])}")
    out.append(
        f" {p('Client ID', DIM)} {p(str(node['client_id']), CYAN, BOLD)}   {p('Account', DIM)} {node['account']}   "
        f"{p('Gateway', DIM)} {node['host']}:{node['port']}   {p('Data', DIM)} {node['market_data']}   {conns}",
    )
    if risk["reasons"]:
        colour = YELLOW if risk["state"] == "REDUCING" else RED
        out.append(" " + p("⚠ " + "; ".join(risk["reasons"]), colour, BOLD))

    if view in ("all", "tickers"):
        out += ["", _section(p, "TICKERS", "LTP turns green ▲ / red ▼ on each move")]
        rows = []
        for t in status["tickers"]:
            move = tracker.move(t["id"]) if tracker else 0
            ltp = price(t["ltp"])
            ltp = p(f"{ltp} ▲", GREEN, BOLD) if move > 0 else p(f"{ltp} ▼", RED, BOLD) if move < 0 else p(f"{ltp}  ", WHITE, BOLD)
            rows.append(
                [p(t["id"], BOLD), ltp, _freshness(p, now_ns, t["ts_ns"]), _position(p, t["position"]),
                 _signed(p, t["upnl"]), p(", ".join(t["strategies"]), DIM)],
            )
        out += table(p, ["Ticker", "LTP", "Updated", "Position", "Unrealised P&L", "Strategies"], rows, "lrrrrl")

    if view in ("all", "strategies"):
        virtual = (om_state or {}).get("virtual", {})
        out += ["", _section(p, "STRATEGIES", f"{len(status['strategies'])} running")]
        rows = []
        for s in status["strategies"]:
            first = True
            for t in s["tickers"]:
                vp = virtual.get(t["id"], {}).get(s["id"], {})
                name = p(s["id"], CYAN, BOLD) if first else ""
                kind = p(human_strategy(s["cls"]), DIM) if first else ""
                tf = human_timeframe(s["timeframe"]) if first else ""
                rows.append(
                    [name, kind, tf, t["id"], _signal(p, t["dir"]), p(ago(now_ns, t["signal_ns"]), DIM),
                     ist_time(t["bar_ns"]), price(t["bar_close"]), _position(p, vp.get("quantity")),
                     _signed(p, vp.get("realized_pnl"))],
                )
                first = False
        out += table(
            p,
            ["Strategy", "Type", "Timeframe", "Ticker", "Signal", "Since", "Last bar", "Bar close", "Position", "Realised P&L"],
            rows,
            "lllllrrrrr",
        )
        details = [f"   {p(s['id'], CYAN)}  {human_params(s['params'])} · size {qty(s['size'])} · {s['signals']} signals"
                   for s in status["strategies"]]
        out += details

    if view in ("all", "activity"):
        n = 5 if view == "all" else 15
        out += ["", _section(p, "RECENT SIGNALS", "times in IST")]
        rows = [[ist_time(sg["ts_ns"]), p(sg["strategy"], CYAN), sg["ticker"], _signal(p, sg["dir"]), p(sg["reason"][:70], DIM)]
                for sg in status["signals"][:n]]
        out += table(p, ["Time", "Strategy", "Ticker", "Signal", "Reason"], rows, "lllll")

        out += ["", _section(p, "RECENT ORDERS")]
        rows = []
        for o in status["orders"][:n]:
            side = p(o["side"], GREEN, BOLD) if o["side"] == "BUY" else p(o["side"], RED, BOLD)
            st = o["status"]
            st = p(st, GREEN) if st == "FILLED" else p(st, RED, BOLD) if st in ("REJECTED", "DENIED") else p(st, YELLOW)
            rows.append([ist_time(o["ts_ns"]), o["ticker"], side, o["type"].lower(), qty(o["qty"]), qty(o["filled"]),
                         price(o["avg_px"]) if o["avg_px"] else "-", st])
        out += table(p, ["Time", "Ticker", "Side", "Type", "Qty", "Filled", "Avg price", "Status"], rows, "llllrrrl")

    return "\n".join(out + ["", _footer(p, view, paused)])


def _footer(p: Paint, view: str, paused: bool) -> str:
    keys = []
    for key, name in VIEWS.items():
        label = f" {key} {VIEW_LABELS[name]} "
        keys.append(p(label, REVERSE, BOLD) if name == view else p(label, DIM))
    state = "  " + p.badge("PAUSED", ON_YELLOW) if paused else ""
    return " " + " ".join(keys) + "   " + p(" p ", BOLD) + p("pause", DIM) + "  " + p(" q ", BOLD) + p("quit", DIM) + state
