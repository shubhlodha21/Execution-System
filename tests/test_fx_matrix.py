import pytest

from execsys.tools.fx_matrix import FxQuote
from execsys.tools.fx_matrix import cross_matrix
from execsys.tools.fx_matrix import render
from execsys.tools.fx_matrix import split_pair
from execsys.tools.fx_monitor import normalize_pair


MIDS = {("EUR", "USD"): 1.08, ("USD", "CHF"): 0.90, ("USD", "JPY"): 150.0}
CCYS = ["EUR", "USD", "CHF", "JPY"]


def test_split_pair():
    assert split_pair("EUR/USD.IDEALPRO") == ("EUR", "USD")


@pytest.mark.parametrize(("raw", "expected"), [
    ("EURUSD", "EUR/USD.IDEALPRO"),
    ("usd/jpy", "USD/JPY.IDEALPRO"),
    ("GBP/USD.IDEALPRO", "GBP/USD.IDEALPRO"),
])
def test_normalize_pair(raw, expected):
    assert normalize_pair(raw) == expected


def test_cross_matrix_direct_inverse_and_cross():
    m = cross_matrix(MIDS, CCYS)
    assert m[("EUR", "EUR")] == 1.0
    assert m[("EUR", "USD")] == pytest.approx(1.08)
    assert m[("CHF", "USD")] == pytest.approx(1 / 0.90)  # inverse: CHFUSD
    assert m[("EUR", "JPY")] == pytest.approx(1.08 * 150)  # cross via USD
    assert m[("JPY", "CHF")] == pytest.approx(0.90 / 150)


def test_cross_matrix_missing_quote_is_none():
    m = cross_matrix({("EUR", "USD"): 1.08}, ["EUR", "USD", "JPY"])
    assert m[("EUR", "JPY")] is None


def test_render_shows_table_and_matrix():
    now = 1_793_000_000_000_000_000
    quotes = {
        "EUR/USD.IDEALPRO": FxQuote(1.07995, 1.08005, now - 500_000_000),
        "USD/JPY.IDEALPRO": FxQuote(149.995, 150.005, now),
    }
    out = render(quotes, ["EUR/USD.IDEALPRO", "USD/CHF.IDEALPRO", "USD/JPY.IDEALPRO"], now)
    assert "EUR/USD" in out and "1.0p" in out  # 1 pip spread
    assert "USD/CHF" in out and "waiting for quotes" in out
    assert "Cross rates" in out
    assert "162.000" in out  # EUR->JPY via USD


def test_render_header_shows_utc_and_ist():
    now = 1_793_100_967_000_000_000  # 2026-10-27 11:36:07 UTC
    header = render({}, ["EUR/USD.IDEALPRO"], now).splitlines()[0]
    assert "2026-10-27 11:36:07 UTC" in header
    assert "2026-10-27 17:06:07 IST" in header  # UTC + 5:30


def test_fx_monitor_node_builds_offline():
    from execsys.app import build_node
    from execsys.config import load_settings
    from execsys.tools.fx_monitor import FxMonitor
    from execsys.tools.fx_monitor import FxMonitorOptions

    settings = load_settings("config/settings.yaml", env={})
    pairs = [normalize_pair(p) for p in ("EURUSD", "USD/CHF", "USDJPY")]
    node = build_node(settings, data_only=True, instruments=pairs)
    node.add_actor(FxMonitor(FxMonitorOptions(pairs)))
    node.dispose()
