"""
Start every command-line entry point for real, so a broken program cannot pass the suite.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "args",
    [
        ["-m", "execsys.app", "--help"],
        ["-m", "execsys.ops", "--help"],
        ["-m", "execsys.dashboard", "--help"],
        ["-m", "execsys.tools.fx_monitor", "--help"],
        ["run.py", "--help"],
    ],
)
def test_entry_point_starts(args):
    result = subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr


def test_dashboard_renders_snapshot_and_live_loop(tmp_path):
    now = 1_793_100_967_000_000_000
    status = {
        "ts_ns": now, "started_ns": now - 60_000_000_000,
        "node": {"name": "EXECSYS", "trader_id": "TRADER-001", "client_id": 99, "host": "127.0.0.1", "port": 4002,
                 "mode": "PAPER", "account": "DU****567", "data_only": False, "market_data": "realtime"},
        "connection": {"IB": "CONNECTED"}, "risk": {"state": "ACTIVE", "reasons": []},
        "tickers": [{"id": "AAPL.XNAS", "ltp": "229.41", "ltp_src": "last", "bid": None, "ask": None,
                     "ts_ns": now, "position": "0", "upnl": None, "strategies": ["EMA-CROSS-001"]}],
        "strategies": [], "signals": [], "orders": [],
    }
    (tmp_path / "status.json").write_text(json.dumps(status))
    cfg = tmp_path / "settings.yaml"
    cfg.write_text(
        f"dashboard: {{status_file: '{(tmp_path / 'status.json').as_posix()}'}}\n"
        f"order_manager: {{state_file: '{(tmp_path / 'om.json').as_posix()}'}}\n",
    )
    once = subprocess.run([sys.executable, "-m", "execsys.dashboard", "--config", str(cfg), "--once", "--no-color"],
                          cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert once.returncode == 0, once.stderr
    assert "AAPL.XNAS" in once.stdout and "229.410" in once.stdout

    # The full-screen loop must keep running (no TTY: it just waits between refreshes)
    loop = subprocess.Popen([sys.executable, "-m", "execsys.dashboard", "--config", str(cfg), "--refresh", "0.2"],
                            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            loop.wait(timeout=3)
    finally:
        loop.kill()
        out, err = loop.communicate()
    assert b"Traceback" not in err, err.decode()
    assert b"AAPL.XNAS" in out
