from decimal import Decimal as D

import pytest

from execsys.contracts import RiskState
from execsys.contracts import Signal
from execsys.order_manager.book import UNATTRIBUTED
from execsys.order_manager.book import SignalBook
from execsys.order_manager.book import VirtualBook
from execsys.order_manager.book import VirtualPosition
from execsys.order_manager.book import plan_order


def sig(strategy: str, direction: int, ts: int = 1, iid: str = "AAPL.XNAS") -> Signal:
    return Signal(strategy, iid, direction, 1.0, "", ts, ts)


def test_signal_direction_validated():
    with pytest.raises(ValueError):
        sig("A", 2)


def test_netting_opposite_signals_cancel():
    book = SignalBook()
    book.update(sig("A", 1), D(10))
    book.update(sig("B", -1), D(10))
    assert book.target("AAPL.XNAS") == 0
    book.update(sig("B", 0, ts=2), D(10))
    assert book.target("AAPL.XNAS") == 10


def test_repeated_signal_is_idempotent():
    book = SignalBook()
    book.update(sig("A", 1, ts=1), D(10))
    book.update(sig("A", 1, ts=2), D(10))
    assert book.target("AAPL.XNAS") == 10


def test_out_of_order_signal_ignored():
    book = SignalBook()
    assert book.update(sig("A", 1, ts=5), D(10))
    assert not book.update(sig("A", -1, ts=3), D(10))
    assert book.target("AAPL.XNAS") == 10


def test_signal_book_roundtrip():
    book = SignalBook()
    book.update(sig("A", -1, ts=7), D("2.5"))
    restored = SignalBook.from_dict(book.to_dict())
    assert restored.target("AAPL.XNAS") == D("-2.5")


@pytest.mark.parametrize(
    ("target", "position", "working", "state", "expected"),
    [
        (D(10), D(0), D(0), RiskState.ACTIVE, D(10)),
        (D(10), D(4), D(0), RiskState.ACTIVE, D(6)),
        (D(-10), D(10), D(0), RiskState.ACTIVE, D(-20)),
        (D(10), D(10), D(0), RiskState.ACTIVE, D(0)),
        (D(10), D(0), D(5), RiskState.ACTIVE, D(0)),  # working order -> wait
        (D(500), D(0), D(0), RiskState.ACTIVE, D(100)),  # clamped to max position
        (D(10), D(0), D(0), RiskState.HALTED, D(0)),
        (D(20), D(10), D(0), RiskState.REDUCING, D(0)),  # cannot add
        (D(-10), D(10), D(0), RiskState.REDUCING, D(-10)),  # reduce to flat, not flip
        (D(5), D(10), D(0), RiskState.REDUCING, D(-5)),
        (D(-5), D(-10), D(0), RiskState.REDUCING, D(5)),
        (D(10), D(0), D(0), RiskState.REDUCING, D(0)),
    ],
)
def test_plan_order(target, position, working, state, expected):
    plan = plan_order(target, position, working, max_position=D(100), risk_state=state)
    assert plan.quantity == expected


def test_virtual_position_average_cost_and_pnl():
    p = VirtualPosition()
    p.apply(D(10), D(100), D(1))
    p.apply(D(10), D(110), D(1))
    assert p.avg_px == D(105)
    p.apply(D(-15), D(120), D(1))
    assert p.quantity == 5
    assert p.realized_pnl == D(225)  # 15 * (120 - 105)
    p.apply(D(-10), D(100), D(1))  # close 5, flip 5 short at 100
    assert p.quantity == -5
    assert p.avg_px == D(100)
    assert p.realized_pnl == D(200)  # + 5 * (100 - 105)


def test_fill_split_pro_rata_between_strategies():
    vb = VirtualBook()
    alloc = vb.allocate_fill("X", D(30), D(10), {"A": D(10), "B": D(20)})
    assert alloc == {"A": D(10), "B": D(20)}
    assert vb.position("X", "A").quantity == 10
    assert vb.position("X", "B").quantity == 20


def test_partial_fill_split_sums_exactly():
    vb = VirtualBook()
    alloc = vb.allocate_fill("X", D(10), D(10), {"A": D(10), "B": D(20)})
    assert sum(alloc.values()) == D(10)
    assert UNATTRIBUTED not in alloc


def test_netted_order_only_moves_strategies_needing_it():
    vb = VirtualBook()
    vb.allocate_fill("X", D(10), D(10), {"A": D(10), "B": D(0)})
    # B turns short while A stays long: the netting sell belongs to B, A is untouched
    alloc = vb.allocate_fill("X", D(-10), D(11), {"A": D(10), "B": D(-10)})
    assert alloc == {"B": D(-10)}


def test_unexpected_fill_goes_to_unattributed():
    vb = VirtualBook()
    alloc = vb.allocate_fill("X", D(5), D(10), {})
    assert alloc == {UNATTRIBUTED: D(5)}


def test_virtual_book_roundtrip():
    vb = VirtualBook()
    vb.allocate_fill("X", D(3), D("10.5"), {"A": D(3)})
    restored = VirtualBook.from_dict(vb.to_dict())
    assert restored.position("X", "A").avg_px == D("10.5")
