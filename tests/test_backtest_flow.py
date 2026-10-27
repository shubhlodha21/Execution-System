"""
End-to-end wiring test on the Nautilus backtest engine (no IB needed):
bars -> EmaCrossSignal -> Signal over the bus -> OrderManager -> Risk Engine -> simulated venue
-> fills -> VirtualBook, with the Risk Monitor, Monitoring actor and Data Recorder running.
"""

import math
from decimal import Decimal as D

from nautilus_trader.backtest import BacktestEngine
from nautilus_trader.common import LoggerConfig
from nautilus_trader.common import LogLevel
from nautilus_trader.config import BacktestEngineConfig
from nautilus_trader.execution import MakerTakerFeeModel
from nautilus_trader.model import AccountType
from nautilus_trader.model import Bar
from nautilus_trader.model import BarType
from nautilus_trader.model import Currency
from nautilus_trader.model import Equity
from nautilus_trader.model import InstrumentId
from nautilus_trader.model import Money
from nautilus_trader.model import OmsType
from nautilus_trader.model import Price
from nautilus_trader.model import Quantity
from nautilus_trader.model import Symbol
from nautilus_trader.model import TraderId
from nautilus_trader.model import Venue

from execsys.alerting.monitor import MonitoringActor
from execsys.app import build_strategy
from execsys.config import Settings
from execsys.contracts import RiskState
from execsys.ops.__main__ import write_command
from execsys.order_manager.manager import OrderManager
from execsys.persistence.recorder import DataRecorder
from execsys.risk.monitor import RiskMonitor


IID = "AAPL.XNAS"
MINUTE_NS = 60_000_000_000
START_NS = 1_767_000_000_000_000_000


EMA_STRATEGY = {
    "strategy_id": "EMA-CROSS-001",
    "class_path": "execsys.strategies.ema_cross:EmaCrossSignal",
    "instruments": [IID],
    "size": "10",
    "params": {"bar_spec": "1-MINUTE-LAST-EXTERNAL", "fast_period": 5, "slow_period": 15, "warmup_days": 0},
}
RSI_STRATEGY = {
    "strategy_id": "RSI-EQ-001",
    "class_path": "execsys.strategies.rsi:RsiSignal",
    "instruments": [IID],
    "size": "10",
    "params": {"bar_spec": "1-MINUTE-LAST-EXTERNAL", "period": 14, "overbought": 70, "oversold": 30,
               "exit_level": 50, "warmup_days": 0},
}


def make_settings(tmp_path, strategy: dict = EMA_STRATEGY) -> Settings:
    return Settings.model_validate(
        {
            "order_manager": {"state_file": str(tmp_path / "om.json"), "min_order_interval_ms": 0},
            "strategies": [strategy],
            "risk_limits": {"max_daily_loss": "1000000", "stale_data_secs": 300},
            "recorder": {"directory": str(tmp_path / "rec"), "flush_interval_secs": 0.05},
            "alerts": {"enabled": False},
            "ops": {"control_file": str(tmp_path / "control.json"), "risk_state_file": str(tmp_path / "risk.json")},
            "dashboard": {"status_file": str(tmp_path / "status.json")},
        },
    )


def make_bars(n: int = 600) -> list[Bar]:
    bar_type = BarType.from_str(f"{IID}-1-MINUTE-LAST-EXTERNAL")
    bars = []
    for i in range(n):
        px = 200 + 10 * math.sin(i / 40)
        o, c = px, 200 + 10 * math.sin((i + 1) / 40)
        ts = START_NS + (i + 1) * MINUTE_NS
        bars.append(
            Bar(
                bar_type,
                Price(o, 2),
                Price(max(o, c) + 0.05, 2),
                Price(min(o, c) - 0.05, 2),
                Price(c, 2),
                Quantity(1000, 0),
                ts,
                ts,
            ),
        )
    return bars


def build_engine(settings: Settings):
    engine = BacktestEngine(
        BacktestEngineConfig(
            trader_id=TraderId.from_str("TESTER-001"),
            logging=LoggerConfig(stdout_level=LogLevel.WARNING),
            run_analysis=False,
        ),
    )
    usd = Currency.from_str("USD")
    engine.add_venue(
        venue=Venue("XNAS"),
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        base_currency=usd,
        starting_balances=[Money(1_000_000, usd)],
        fee_model=MakerTakerFeeModel(maker_rate=D(0), taker_rate=D(0)),
    )
    instrument = Equity(
        instrument_id=InstrumentId.from_str(IID),
        raw_symbol=Symbol("AAPL"),
        currency=usd,
        price_precision=2,
        price_increment=Price.from_str("0.01"),
        ts_event=0,
        ts_init=0,
        lot_size=Quantity.from_int(1),
    )
    engine.add_instrument(instrument)
    engine.add_data(make_bars())

    strategy = build_strategy(settings.strategies[0])
    om = OrderManager(settings)
    risk = RiskMonitor(settings)
    recorder = DataRecorder(settings)
    for component in (strategy, om, recorder):
        component.client_id = None  # route to the simulated venue instead of IB
    engine.add_actor(MonitoringActor(settings))
    engine.add_actor(recorder)
    engine.add_actor(strategy)
    engine.add_strategy(risk)
    engine.add_strategy(om)
    return engine, om, risk


def test_signal_to_fill_flow(tmp_path):
    engine, om, risk = build_engine(make_settings(tmp_path))
    try:
        engine.run()

        fills = engine.generate_order_fills_report()
        assert len(fills) >= 3, "expected several crossover trades"

        # Order Manager kept the real position on the signal target and attributed every fill
        net = D(engine.portfolio.net_position(InstrumentId.from_str(IID)))
        assert net == om.signals.target(IID)
        assert abs(net) == D(10)
        virtual = om.virtual.positions(IID)
        assert set(virtual) == {"EMA-CROSS-001"}
        assert virtual["EMA-CROSS-001"].quantity == net
        assert risk.state is RiskState.ACTIVE

        # Recorder wrote the bars through its background writer
        bars_csv = list((tmp_path / "rec").rglob("bar.csv"))
        assert bars_csv and len(bars_csv[0].read_text().splitlines()) > 100
    finally:
        engine.dispose()


def test_manual_halt_blocks_all_orders(tmp_path):
    settings = make_settings(tmp_path)
    write_command(settings.ops.control_file, "halt")
    engine, om, risk = build_engine(settings)
    try:
        engine.run()
        assert len(engine.generate_order_fills_report()) == 0
        assert om.signals.target(IID) != 0  # signals still arrived, they were just not traded
        assert risk.state is RiskState.HALTED
    finally:
        engine.dispose()


def test_rsi_strategy_flow(tmp_path):
    engine, om, risk = build_engine(make_settings(tmp_path, RSI_STRATEGY))
    try:
        engine.run()
        fills = engine.generate_order_fills_report()
        assert len(fills) >= 3, "expected RSI entries and exits on the sine wave"
        net = D(engine.portfolio.net_position(InstrumentId.from_str(IID)))
        assert net == om.signals.target(IID)
        assert abs(net) in (D(0), D(10))
        assert set(om.virtual.positions(IID)) == {"RSI-EQ-001"}
        assert om.virtual.positions(IID)["RSI-EQ-001"].quantity == net
        assert risk.state is RiskState.ACTIVE
    finally:
        engine.dispose()


def test_status_publisher_snapshot_and_dashboard(tmp_path):
    import json

    from execsys.dashboard.publisher import StatusPublisher
    from execsys.dashboard.publisher import StatusPublisherConfig
    from execsys.dashboard.render import build

    settings = make_settings(tmp_path)
    engine, om, risk = build_engine(settings)
    engine.add_strategy(StatusPublisher(StatusPublisherConfig(settings)))
    try:
        engine.run()
    finally:
        engine.dispose()

    status = json.loads((tmp_path / "status.json").read_text())
    assert status["stopped"] is True
    assert status["node"]["client_id"] == settings.ib.client_id
    assert status["node"]["mode"] == "PAPER"
    ticker = status["tickers"][0]
    assert ticker["id"] == IID and ticker["strategies"] == ["EMA-CROSS-001"]
    strat = status["strategies"][0]
    assert strat["id"] == "EMA-CROSS-001" and strat["signals"] > 0
    assert strat["tickers"][0]["bar_close"] is not None
    assert status["signals"] and status["orders"]
    assert status["orders"][0]["status"] == "FILLED"

    om_state = json.loads((tmp_path / "om.json").read_text())
    screen = build(status, om_state, status["ts_ns"], color=False)
    assert "Client ID 99" in screen and "EMA-CROSS-001" in screen and "FILLED" in screen
    (tmp_path / "screen.txt").write_text(screen, encoding="utf-8")
    (tmp_path / "screen_color.txt").write_text(build(status, om_state, status["ts_ns"]), encoding="utf-8")
