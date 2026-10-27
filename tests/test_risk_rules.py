from decimal import Decimal as D

from execsys.config import RiskLimits
from execsys.contracts import RiskState
from execsys.risk.rules import RiskSnapshot
from execsys.risk.rules import evaluate


LIMITS = RiskLimits(max_daily_loss=D(1000), default_max_position=D(100), max_gross_exposure=D(50000))


def snap(**kw) -> RiskSnapshot:
    base = dict(
        daily_pnl=D(0),
        positions={"AAPL.XNAS": D(0)},
        gross_exposure=D(0),
        data_age_secs={"AAPL.XNAS": 1.0},
        rejects_in_window=0,
    )
    base.update(kw)
    return RiskSnapshot(**base)


def ev(s: RiskSnapshot):
    return evaluate(s, LIMITS, lambda _iid: D(100))


def test_all_clear():
    assert ev(snap()).state is RiskState.ACTIVE


def test_daily_loss_halts():
    d = ev(snap(daily_pnl=D(-1000)))
    assert d.state is RiskState.HALTED
    assert "daily loss" in d.reasons[0]


def test_position_limit_reduces():
    assert ev(snap(positions={"AAPL.XNAS": D(-101)})).state is RiskState.REDUCING


def test_gross_exposure_reduces():
    assert ev(snap(gross_exposure=D(60000))).state is RiskState.REDUCING


def test_rejects_halt():
    assert ev(snap(rejects_in_window=5)).state is RiskState.HALTED


def test_stale_data_only_matters_with_position():
    assert ev(snap(data_age_secs={"AAPL.XNAS": None})).state is RiskState.ACTIVE
    stale = snap(positions={"AAPL.XNAS": D(10)}, data_age_secs={"AAPL.XNAS": 500.0})
    assert ev(stale).state is RiskState.REDUCING


def test_worst_state_wins_and_all_reasons_kept():
    d = ev(snap(daily_pnl=D(-5000), positions={"AAPL.XNAS": D(500)}))
    assert d.state is RiskState.HALTED
    assert len(d.reasons) == 2


def test_manual_state():
    assert ev(snap(manual=RiskState.REDUCING)).state is RiskState.REDUCING
