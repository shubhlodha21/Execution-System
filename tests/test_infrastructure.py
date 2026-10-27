import json
import time
from decimal import Decimal as D

import pytest

from execsys.alerting.dispatcher import AlertDispatcher
from execsys.alerting.dispatcher import AlertFilter
from execsys.config import AlertSettings
from execsys.config import load_settings
from execsys.contracts import Alert
from execsys.contracts import Severity
from execsys.ops.__main__ import write_command
from execsys.persistence.state_store import JsonStateStore
from execsys.persistence.writer import BatchWriter
from execsys.persistence.writer import CsvSink
from execsys.strategies.ema_cross import crossover


def test_repo_settings_load():
    s = load_settings("config/settings.yaml", env={"IB_ACCOUNT_ID": "DU1"})
    assert s.ib.account_id == "DU1"
    assert s.universe == ["AAPL.XNAS", "MSFT.XNAS", "TSLA.XNAS"]
    assert s.max_position("TSLA.XNAS") == D(50)
    assert s.max_position("AAPL.XNAS") == D(100)


def test_settings_reject_bad_client_id(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text("ib: {client_id: 2000}\n")
    with pytest.raises(ValueError):
        load_settings(p, env={})


def test_settings_reject_duplicate_strategy_ids(tmp_path):
    p = tmp_path / "s.yaml"
    strat = "{strategy_id: A, class_path: 'x:Y', instruments: [AAPL.XNAS], size: '1'}"
    p.write_text(f"strategies: [{strat}, {strat}]\n")
    with pytest.raises(ValueError):
        load_settings(p, env={})


def test_crossover():
    assert crossover(None, 1.0) is None
    assert crossover(-0.1, 0.2) == 1
    assert crossover(0.1, -0.2) == -1
    assert crossover(0.1, 0.2) is None


def test_batch_writer_flushes_on_stop(tmp_path):
    w = BatchWriter(CsvSink(tmp_path), queue_size=100, batch_size=1000, flush_interval=60)
    w.start()
    for i in range(5):
        w.put(("tick", "QUOTE", "EQUITY", "AAPL.XNAS", "t", str(i), "1.0", "", "", ""))
    w.stop()
    files = list(tmp_path.rglob("tick.csv"))
    assert len(files) == 1
    lines = files[0].read_text().splitlines()
    assert lines[0].startswith("schema,tick_type")
    assert len(lines) == 6
    assert w.written == 5


def test_batch_writer_drops_when_full(tmp_path):
    w = BatchWriter(CsvSink(tmp_path), queue_size=2, batch_size=10, flush_interval=60)
    for _ in range(5):  # not started: nothing drains the queue
        w.put(("tick",))
    assert w.dropped == 3


def test_state_store_roundtrip(tmp_path):
    store = JsonStateStore(tmp_path / "s.json")
    store.start()
    store.save({"a": 1})
    store.save({"a": 2})
    store.stop()
    assert store.load() == {"a": 2}


def test_alert_filter_dedupes_and_rate_limits():
    f = AlertFilter(dedupe_secs=60, max_per_minute=2)
    a = Alert(Severity.WARNING, "m", "x", 0)
    assert f.allow(a, 0)
    assert not f.allow(a, 1)
    assert f.allow(Alert(Severity.WARNING, "m", "y", 0), 2)
    assert not f.allow(Alert(Severity.WARNING, "m", "z", 0), 3)  # rate limited
    assert f.allow(Alert(Severity.CRITICAL, "m", "z", 0), 4)  # critical bypasses
    assert f.allow(a, 100)  # dedupe window expired


def test_alert_dispatcher_delivers_on_worker_thread():
    got = []
    d = AlertDispatcher(AlertSettings(), channels=[got.append])
    d.start()
    d.submit(Alert(Severity.INFO, "m", "hello", 0))
    d.stop()
    assert [a.message for a in got] == ["hello"]


def test_ops_command_file(tmp_path):
    p = tmp_path / "control.json"
    write_command(p, "halt")
    data = json.loads(p.read_text())
    assert data["command"] == "halt"
    assert data["ts"] <= time.time()
