"""
Risk Monitor: account-level risk on a timer (not per tick).

Reads Portfolio, Cache and order issues, evaluates `rules.evaluate`, and publishes
RiskStateChanged on change. The Order Manager enforces the state (Nautilus v2 does not let Python
switch the Risk Engine's trading state). A HALTED state latches until a manual `resume`.

This is a Nautilus Strategy only because DataActor has no portfolio access; it never sends orders.
"""

from __future__ import annotations

import datetime as dt
import json
from collections import deque
from decimal import Decimal
from pathlib import Path
from typing import Any

from nautilus_trader.model import BarType
from nautilus_trader.model import InstrumentId
from nautilus_trader.model import StrategyId
from nautilus_trader.trading import Strategy
from nautilus_trader.trading import StrategyConfig

from execsys.config import Settings
from execsys.contracts import TOPIC_ALERT
from execsys.contracts import TOPIC_ORDER_ISSUE
from execsys.contracts import TOPIC_RISK_STATE
from execsys.contracts import Alert
from execsys.contracts import OrderIssue
from execsys.contracts import RiskState
from execsys.contracts import RiskStateChanged
from execsys.contracts import Severity
from execsys.persistence.state_store import JsonStateStore
from execsys.risk.rules import RiskSnapshot
from execsys.risk.rules import evaluate
from execsys.telemetry import get_metrics


_TIMER = "risk_monitor.check"
_STATE_CODE = {RiskState.ACTIVE: 0, RiskState.REDUCING: 1, RiskState.HALTED: 2}


class RiskMonitor(Strategy):
    def __init__(self, settings: Settings) -> None:
        super().__init__(StrategyConfig(strategy_id=StrategyId.from_str("RISK-MONITOR"), order_id_tag="RM"))
        self.settings = settings
        self.limits = settings.risk_limits
        self.instrument_ids = [InstrumentId.from_str(i) for i in settings.universe]
        # Bars count as market data for staleness, so bar-only strategies are not flagged
        self._bar_types: dict[InstrumentId, list[BarType]] = {}
        for s in settings.enabled_strategies:
            spec = s.params.get("bar_spec")
            for i in s.instruments if spec else ():
                self._bar_types.setdefault(InstrumentId.from_str(i), []).append(BarType.from_str(f"{i}-{spec}"))
        self.state = RiskState.ACTIVE
        self.reasons: tuple[str, ...] = ()
        self._halt_latched = False
        self._manual: RiskState | None = None
        self._rejects: deque[int] = deque()
        self._control_path = Path(settings.ops.control_file)
        self._control_mtime = 0.0
        self.store = JsonStateStore(settings.ops.risk_state_file)
        self._day = ""
        self._pnl_baseline = Decimal(0)
        self._last: dict[str, float] = {"daily_pnl": 0.0, "gross_exposure": 0.0}
        get_metrics().register_gauge("risk_state", lambda: [(float(_STATE_CODE[self.state]), {})])
        get_metrics().register_gauge("daily_pnl", lambda: [(self._last["daily_pnl"], {})])
        get_metrics().register_gauge("gross_exposure", lambda: [(self._last["gross_exposure"], {})])

    def on_start(self) -> None:
        saved = self.store.load()
        self._day = saved.get("day", "")
        self._pnl_baseline = Decimal(saved.get("pnl_baseline", "0"))
        self._halt_latched = bool(saved.get("halt_latched", False)) and self._day == self._today()
        self._manual = RiskState(saved["manual"]) if saved.get("manual") else None
        self.store.start()
        self.subscribe_topic(TOPIC_ORDER_ISSUE, self._on_order_issue)
        self.clock.set_timer(
            _TIMER,
            dt.timedelta(milliseconds=self.limits.check_interval_ms),
            callback=self._check,
        )

    def on_stop(self) -> None:
        self.clock.cancel_timer(_TIMER)
        self._persist()
        self.store.stop()

    def _on_order_issue(self, message: object) -> None:
        if isinstance(message, OrderIssue):
            self._rejects.append(message.ts)

    def _check(self, _event: Any) -> None:
        try:
            self._read_control()
            snap = self._snapshot()
            decision = evaluate(snap, self.limits, self.settings.max_position)
            state, reasons = decision.state, tuple(decision.reasons)
        except Exception as e:  # fail safe: a broken monitor must not allow new risk
            state, reasons = RiskState.REDUCING, (f"risk monitor error: {type(e).__name__}: {e}",)
            self.log.error(reasons[0])

        if state is RiskState.HALTED:
            self._halt_latched = True
        elif self._halt_latched:
            state, reasons = RiskState.HALTED, (*reasons, "halt latched until manual resume")
        self._set_state(state, reasons)

    def _snapshot(self) -> RiskSnapshot:
        now = self.clock.timestamp_ns()
        today = self._today()
        total_pnl = Decimal(0)
        gross = Decimal(0)
        positions: dict[str, Decimal] = {}
        ages: dict[str, float | None] = {}
        for iid in self.instrument_ids:
            pnl = self.portfolio.total_pnl(iid)
            if pnl is not None:
                total_pnl += pnl.as_decimal()
            exposure = self.portfolio.net_exposure(iid)
            if exposure is not None:
                gross += abs(exposure.as_decimal())
            positions[str(iid)] = Decimal(self.portfolio.net_position(iid))
            ages[str(iid)] = self._data_age(iid, now)

        if self._day != today:  # new trading day: reset the daily PnL baseline
            self._day, self._pnl_baseline, self._halt_latched = today, total_pnl, False
            self._persist()
        daily = total_pnl - self._pnl_baseline

        window_start = now - self.limits.reject_window_secs * 1_000_000_000
        while self._rejects and self._rejects[0] < window_start:
            self._rejects.popleft()

        self._last = {"daily_pnl": float(daily), "gross_exposure": float(gross)}
        return RiskSnapshot(
            daily_pnl=daily,
            positions=positions,
            gross_exposure=gross,
            data_age_secs=ages,
            rejects_in_window=len(self._rejects),
            manual=self._manual,
        )

    def _data_age(self, iid: InstrumentId, now: int) -> float | None:
        latest = [self.cache.quote(iid), self.cache.trade(iid)]
        latest += [self.cache.bar(bt) for bt in self._bar_types.get(iid, ())]
        ts = [d.ts_init for d in latest if d is not None]
        return (now - max(ts)) / 1e9 if ts else None

    def _read_control(self) -> None:
        """
        Manual commands from `python -m execsys.ops` (halt / reduce / resume / flatten).
        Only re-read when the file changes, so the per-check cost is one stat().
        """
        try:
            mtime = self._control_path.stat().st_mtime
        except FileNotFoundError:
            return
        if mtime == self._control_mtime:
            return
        self._control_mtime = mtime
        command = json.loads(self._control_path.read_text(encoding="utf-8")).get("command", "")
        self.log.warning(f"Manual risk command: {command}")
        if command == "halt":
            self._manual = RiskState.HALTED
        elif command == "reduce":
            self._manual = RiskState.REDUCING
        elif command == "resume":
            self._manual, self._halt_latched, self._rejects = None, False, deque()
        elif command == "flatten":
            self._manual = RiskState.HALTED
            self._publish(RiskState.HALTED, ("manual flatten",), flatten=True)
        self._persist()

    def _set_state(self, state: RiskState, reasons: tuple[str, ...]) -> None:
        if state is self.state and reasons == self.reasons:
            return
        changed = state is not self.state
        self.state, self.reasons = state, reasons
        if changed:
            flatten = state is RiskState.HALTED and self.limits.flatten_on_halt
            self._publish(state, reasons, flatten)
            self._persist()

    def _publish(self, state: RiskState, reasons: tuple[str, ...], flatten: bool) -> None:
        now = self.clock.timestamp_ns()
        self.log.warning(f"RISK STATE {state}: {'; '.join(reasons) or 'all clear'}")
        self.publish_message(TOPIC_RISK_STATE, RiskStateChanged(state, reasons, flatten, now))
        severity = Severity.INFO if state is RiskState.ACTIVE else Severity.CRITICAL
        self.publish_message(
            TOPIC_ALERT,
            Alert(severity, "risk_monitor", f"Risk state {state}: {'; '.join(reasons) or 'all clear'}", now),
        )

    def _persist(self) -> None:
        self.store.save(
            {
                "day": self._day,
                "pnl_baseline": str(self._pnl_baseline),
                "halt_latched": self._halt_latched,
                "manual": self._manual.value if self._manual else None,
            },
        )

    def _today(self) -> str:
        return self.clock.utc_now().date().isoformat()
