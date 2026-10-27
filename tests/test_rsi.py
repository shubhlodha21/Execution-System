import pytest

from execsys.config import StrategySettings
from execsys.persistence.recorder import bar_spec_for
from execsys.strategies.rsi import RsiSignal
from execsys.strategies.rsi import rsi_signal


@pytest.mark.parametrize(
    ("rsi", "current", "exit_level", "expected"),
    [
        (25.0, 0, 50.0, 1),     # oversold -> long
        (30.0, 0, 50.0, 1),     # at the level counts
        (75.0, 0, 50.0, -1),    # overbought -> short
        (75.0, 1, 50.0, -1),    # long flips short at overbought
        (45.0, 1, 50.0, None),  # long held below exit level
        (52.0, 1, 50.0, 0),     # long exits at 50
        (48.0, -1, 50.0, 0),    # short exits at 50
        (52.0, -1, 50.0, None), # short held above exit level
        (52.0, 1, None, None),  # no exit level: hold until opposite extreme
        (50.0, 0, 50.0, None),  # flat stays flat in the middle
    ],
)
def test_rsi_signal(rsi, current, exit_level, expected):
    assert rsi_signal(rsi, current, overbought=70, oversold=30, exit_level=exit_level) == expected


def _settings(**params):
    return StrategySettings(
        strategy_id="RSI-T-001",
        class_path="execsys.strategies.rsi:RsiSignal",
        instruments=["EUR/USD.IDEALPRO"],
        size="20000",
        params=params,
    )


def test_rsi_params_validated():
    with pytest.raises(ValueError):
        RsiSignal(_settings(overbought=30, oversold=70))
    with pytest.raises(ValueError):
        RsiSignal(_settings(exit_level=80))
    s = RsiSignal(_settings(bar_spec="5-MINUTE-MID-EXTERNAL", exit_level=None))
    assert s.exit_level is None
    assert str(next(iter(s.states.values())).bar_type) == "EUR/USD.IDEALPRO-5-MINUTE-MID-EXTERNAL"


def test_recorder_uses_mid_bars_for_fx():
    assert bar_spec_for("FX", "1-MINUTE-LAST-EXTERNAL") == "1-MINUTE-MID-EXTERNAL"
    assert bar_spec_for("EQUITY", "1-MINUTE-LAST-EXTERNAL") == "1-MINUTE-LAST-EXTERNAL"
