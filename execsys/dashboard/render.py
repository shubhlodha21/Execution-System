"""
Pure text rendering of a status snapshot for the terminal dashboard (no Nautilus imports, unit-tested).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from decimal import InvalidOperation
from typing import Any

from execsys.tools.fx_matrix import IST


STALE_SECS = 5.0
RESET, BOLD, DIM = "\x1b[0m", "\x1b[1m", "\x1b[2m"
GREEN, RED, YELLOW, CYAN = "\x1b[32m", "\x1b[31m", "\x1b[33m", "\x1b[36m"


class _Paint:
    def __init__(self, color: bool) -> None:
        self.on = color

    def __call__(self, text: str, *codes: str) -> str:
        return f"{''.join(codes)}{text}{RESET}" if self.on and codes else text


def _times(ns: int) -> str:
    t = dt.datetime.fromtimestamp(ns / 1e9, dt.UTC)
    return f"{t:%Y-%m-%d %H:%M:%S} UTC | {t.astimezone(IST):%H:%M:%S} IST"


def _hms(ns: int) -> str:
    if not ns:
        return "-"
    return dt.datetime.fromtimestamp(ns / 1e9, dt.UTC).astimezone(IST).strftime("%H:%M:%S")


def _age(now_ns: int, ns: int) -> str:
    if not ns:
        return "-"
    secs = max(0.0, (now_ns - ns) / 1e9)
    if secs < 60:
        return f"{secs:.0f}s"
    if secs < 3600:
        return f"{secs / 60:.0f}m"
    return f"{secs / 3600:.1f}h"


def _uptime(secs: float) -> str:
    h, rem = divmod(int(secs), 3600)
    return f"{h}h {rem // 60:02d}m"


def _num(value: str | None, places: int | None = None) -> str:
    if value in (None, ""):
        return "-"
    try:
        d = Decimal(value)
    except InvalidOperation:
        return str(value)
    if places is not None:
        return f"{d:,.{places}f}"
    return f"{d.normalize():f}" if d == d.to_integral() else str(value)


def _price(value: str | None) -> str:
    if value in (None, ""):
        return "-"
    d = Decimal(value)
    return f"{d:,.3f}" if abs(d) >= 100 else f"{d:.5f}" if abs(d) < 10 else f"{d:,.4f}"


def _dir(paint: _Paint, direction: int | None) -> str:
    return {1: paint("LONG ", GREEN), -1: paint("SHORT", RED), 0: paint("FLAT ", DIM)}.get(direction, paint("  -  ", DIM))


def _signed(paint: _Paint, value: str | None, places: int = 2) -> str:
    if value in (None, ""):
        return "-"
    d = Decimal(value)
    text = f"{d:+,.{places}f}"
    return paint(text, GREEN) if d > 0 else paint(text, RED) if d < 0 else text


def render(status: dict[str, Any] | None, om_state: dict[str, Any] | None, now_ns: int, color: bool = True) -> str:
    """
    Full dashboard screen. `status` is runtime/status.json, `om_state` the Order Manager state file.
    """
    p = _Paint(color)
    out: list[str] = []
    if not status:
        return "\n".join(
            [
                p("EXECSYS DASHBOARD", BOLD),
                "",
                p("No status yet: start the trading app (python -m execsys.app) and keep dashboard.enabled: true.", YELLOW),
                f"Now: {_times(now_ns)}",
            ],
        )

    node, risk = status["node"], status["risk"]
    age_s = (now_ns - status["ts_ns"]) / 1e9
    if status.get("stopped"):
        app = p("STOPPED", RED, BOLD)
    elif age_s > STALE_SECS:
        app = p(f"NOT RESPONDING ({age_s:.0f}s)", RED, BOLD)
    else:
        app = p("RUNNING", GREEN, BOLD)
    risk_color = {"ACTIVE": GREEN, "REDUCING": YELLOW, "HALTED": RED}.get(risk["state"], YELLOW)
    conn = ", ".join(f"{k} {p(v, GREEN if v == 'CONNECTED' else RED)}" for k, v in status["connection"].items())
    mode = p(node["mode"], YELLOW if node["mode"] == "PAPER" else RED, BOLD)
    uptime = _uptime((status["ts_ns"] - status["started_ns"]) / 1e9) if status.get("started_ns") else "-"

    out.append(p(f"EXECSYS DASHBOARD  {node['name']} / {node['trader_id']}", BOLD) + f"   {_times(now_ns)}")
    out.append(
        f"App {app}   Risk {p(risk['state'], risk_color, BOLD)}   Mode {mode}"
        + (p("  DATA-ONLY", CYAN) if node.get("data_only") else "")
        + f"   Uptime {uptime}   Snapshot {_age(now_ns, status['ts_ns'])} ago",
    )
    out.append(
        f"IB {node['host']}:{node['port']}   Client ID {p(str(node['client_id']), CYAN, BOLD)}   "
        f"Account {node['account']}   Market data {node['market_data']}   Connection {conn}",
    )
    if risk["reasons"]:
        out.append(p("Risk reasons: " + "; ".join(risk["reasons"]), risk_color))

    # Tickers
    out += ["", p("TICKERS", BOLD)]
    out.append(f"{'Ticker':<20}{'LTP':>13}{'Src':>5}{'Bid':>13}{'Ask':>13}{'Age':>6}{'Position':>11}{'Unrl PnL':>12}  Strategies")
    out.append("-" * 118)
    for t in status["tickers"]:
        out.append(
            f"{t['id']:<20}{p(f'{_price(t['ltp']):>13}', BOLD)}{t['ltp_src']:>5}{_price(t['bid']):>13}{_price(t['ask']):>13}"
            f"{_age(now_ns, t['ts_ns']):>6}{_num(t['position']):>11}{_signed(p, t['upnl']):>12}  {', '.join(t['strategies'])}",
        )

    # Strategies
    virtual = (om_state or {}).get("virtual", {})
    out += ["", p("STRATEGIES", BOLD)]
    for s in status["strategies"]:
        out.append(
            p(f"{s['id']}", CYAN, BOLD)
            + f"  {s['cls']}  TF {s['timeframe']}  size {_num(s['size'])}  {s['params']}  signals {s['signals']}",
        )
        out.append(f"   {'Ticker':<20}{'Signal':<7}{'Since':>9}{'Last bar':>10}{'Bar close':>13}{'Virt pos':>11}{'Real PnL':>11}  Last reason")
        for t in s["tickers"]:
            vp = virtual.get(t["id"], {}).get(s["id"], {})
            out.append(
                f"   {t['id']:<20}{_dir(p, t['dir'])}  {_hms(t['signal_ns']):>9}{_hms(t['bar_ns']):>10}{_price(t['bar_close']):>13}"
                f"{_num(vp.get('quantity')):>11}{_signed(p, vp.get('realized_pnl')):>11}  {p(t['reason'][:48], DIM)}",
            )

    # Recent signals and orders
    out += ["", p("RECENT SIGNALS", BOLD)]
    if not status["signals"]:
        out.append(p("   none yet", DIM))
    for sg in status["signals"][:5]:
        out.append(f"   {_hms(sg['ts_ns'])}  {sg['strategy']:<16}{sg['ticker']:<20}{_dir(p, sg['dir'])}  {p(sg['reason'][:60], DIM)}")

    out += ["", p("RECENT ORDERS", BOLD)]
    if not status["orders"]:
        out.append(p("   none yet", DIM))
    else:
        out.append(f"   {'Time':<10}{'Ticker':<20}{'Side':<6}{'Type':<8}{'Qty':>10}{'Filled':>10}{'Avg px':>13}  Status")
    for o in status["orders"][:6]:
        side = p(f"{o['side']:<6}", GREEN if o["side"] == "BUY" else RED)
        st_color = GREEN if o["status"] == "FILLED" else RED if o["status"] in ("REJECTED", "DENIED") else YELLOW
        out.append(
            f"   {_hms(o['ts_ns']):<10}{o['ticker']:<20}{side}{o['type']:<8}{_num(o['qty']):>10}{_num(o['filled']):>10}"
            f"{_price(o['avg_px']):>13}  {p(o['status'], st_color)}",
        )
    out += ["", p("Ctrl+C to exit  |  times in IST  |  control: python -m execsys.ops halt|resume|status", DIM)]
    return "\n".join(out)
