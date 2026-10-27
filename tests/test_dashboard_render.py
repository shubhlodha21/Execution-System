from execsys.dashboard.publisher import mask_account
from execsys.dashboard.render import LtpTracker
from execsys.dashboard.render import build
from execsys.dashboard.render import clip
from execsys.dashboard.render import human_params
from execsys.dashboard.render import human_timeframe
from execsys.dashboard.render import vlen


NOW = 1_793_100_967_000_000_000  # 2026-10-27 11:36:07 UTC


def _status(**overrides):
    status = {
        "ts_ns": NOW - 1_000_000_000, "started_ns": NOW - 3_600_000_000_000,
        "node": {"name": "EXECSYS", "trader_id": "TRADER-001", "client_id": 99, "host": "127.0.0.1", "port": 4002,
                 "mode": "PAPER", "account": "DU****567", "data_only": False, "market_data": "realtime"},
        "connection": {"IB": "CONNECTED"},
        "risk": {"state": "ACTIVE", "reasons": []},
        "tickers": [{"id": "EUR/USD.IDEALPRO", "ltp": "1.11464", "ltp_src": "mid", "bid": "1.11463", "ask": "1.11465",
                     "ts_ns": NOW - 1_000_000_000, "position": "20000", "upnl": "-12.5", "strategies": ["RSI-FX-001"]}],
        "strategies": [{"id": "RSI-FX-001", "cls": "RsiSignal", "timeframe": "5-MINUTE-MID-EXTERNAL", "size": "20000",
                        "params": "period=14, overbought=70, oversold=30, exit_level=50", "signals": 2,
                        "tickers": [{"id": "EUR/USD.IDEALPRO", "dir": 1, "signal_ns": NOW - 300_000_000_000,
                                     "reason": "rsi14=27.4", "bar_close": "1.11460", "bar_ns": NOW - 60_000_000_000}]}],
        "signals": [{"ts_ns": NOW - 300_000_000_000, "strategy": "RSI-FX-001", "ticker": "EUR/USD.IDEALPRO", "dir": 1,
                     "reason": "rsi14=27.4"}],
        "orders": [{"ts_ns": NOW - 299_000_000_000, "strategy": "ORDER-MANAGER-001", "ticker": "EUR/USD.IDEALPRO",
                    "side": "BUY", "type": "MARKET", "qty": "20000", "filled": "20000", "avg_px": "1.11462",
                    "status": "FILLED"}],
    }
    status.update(overrides)
    return status


def test_waiting_screen():
    assert "Waiting for the trading app" in build(None, None, NOW, color=False)


def test_all_view_plain_text():
    screen = build(_status(), None, NOW, color=False)
    assert "[● RUNNING]" in screen and "[RISK ACTIVE]" in screen and "[PAPER]" in screen
    assert "Client ID 99" in screen and "17:06:07 IST" in screen
    assert "1.11464" in screen and "Bid" not in screen  # only LTP for prices
    assert "5 min · mid" in screen and "RSI overbought/oversold" in screen
    assert "[LONG]" in screen and "5m ago" in screen and "FILLED" in screen
    assert "period 14 · OB 70 · OS 30 · exit 50" in screen


def test_views_select_sections():
    s = _status()
    assert "TICKERS" in build(s, None, NOW, view="tickers", color=False)
    assert "STRATEGIES" not in build(s, None, NOW, view="tickers", color=False)
    assert "RECENT ORDERS" in build(s, None, NOW, view="activity", color=False)
    assert "TICKERS" not in build(s, None, NOW, view="activity", color=False)


def test_stale_halted_and_disconnected():
    s = _status(ts_ns=NOW - 30_000_000_000, connection={"IB": "DISCONNECTED"},
                risk={"state": "HALTED", "reasons": ["daily loss -1200 <= -1000"]})
    screen = build(s, None, NOW, color=False)
    assert "NOT RESPONDING 30s" in screen and "[RISK HALTED]" in screen
    assert "daily loss" in screen and "disconnected" in screen


def test_ltp_arrows_and_colors():
    tracker = LtpTracker()
    tracker.update(_status()["tickers"])
    up = _status(tickers=[{**_status()["tickers"][0], "ltp": "1.11470"}])
    tracker.update(up["tickers"])
    assert "1.11470 ▲" in build(up, None, NOW, tracker=tracker, color=False)
    colored = build(up, None, NOW, tracker=tracker, color=True)
    assert "\x1b[32m" in colored  # green
    assert vlen("\x1b[32mabc\x1b[0m") == 3


def test_table_columns_align_with_colors():
    lines = build(_status(), None, NOW, view="tickers", color=True).splitlines()
    rows = [line for line in lines if "│" in line]
    assert len({vlen(r) for r in rows}) == 1  # every row the same visible width


def test_human_labels():
    assert human_timeframe("1-MINUTE-LAST-EXTERNAL") == "1 min · last"
    assert human_params("fast_period=10, slow_period=30") == "fast 10 · slow 30"
    assert mask_account("DU1234567") == "DU****567"


def test_clip_keeps_colours_and_width():
    line = "[32m" + "x" * 50 + "[0m" + "y" * 50
    out = clip(line, 40)
    assert vlen(out) == 40 and out.endswith("…") and "[32m" in out
    assert clip("short", 40) == "short"
