from execsys.dashboard.publisher import mask_account
from execsys.dashboard.render import render


NOW = 1_793_100_967_000_000_000


def test_render_without_status():
    assert "No status yet" in render(None, None, NOW, color=False)


def test_render_marks_stale_and_halted():
    status = {
        "ts_ns": NOW - 30_000_000_000, "started_ns": NOW - 3_600_000_000_000,
        "node": {"name": "EXECSYS", "trader_id": "TRADER-001", "client_id": 99, "host": "127.0.0.1", "port": 4002,
                 "mode": "PAPER", "account": "DU****567", "data_only": False, "market_data": "realtime"},
        "connection": {"IB": "DISCONNECTED"},
        "risk": {"state": "HALTED", "reasons": ["daily loss -1200 <= -1000"]},
        "tickers": [{"id": "EUR/USD.IDEALPRO", "ltp": "1.11464", "ltp_src": "mid", "bid": "1.11463", "ask": "1.11465",
                     "ts_ns": NOW - 1_000_000_000, "position": "20000", "upnl": "-12.5", "strategies": ["RSI-FX-001"]}],
        "strategies": [], "signals": [], "orders": [],
    }
    screen = render(status, None, NOW, color=False)
    assert "NOT RESPONDING (30s)" in screen
    assert "Risk HALTED" in screen and "daily loss" in screen
    assert "DISCONNECTED" in screen
    assert "1.11464" in screen and "-12.50" in screen
    assert "17:06:07 IST" in screen


def test_mask_account():
    assert mask_account("DU1234567") == "DU****567"
    assert mask_account(None) == "-"
