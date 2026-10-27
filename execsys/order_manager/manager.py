"""
Order Manager: the only component that places orders.

signals (all strategies) -> net target per instrument -> plan vs. position/working orders/risk
state -> submit_order -> Risk Engine -> Execution Engine -> IB.
Fills are attributed back to strategies in a VirtualBook for per-strategy position and PnL.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from nautilus_trader.model import ClientId
from nautilus_trader.model import InstrumentId
from nautilus_trader.model import OrderSide
from nautilus_trader.model import StrategyId
from nautilus_trader.model import TimeInForce
from nautilus_trader.trading import Strategy
from nautilus_trader.trading import StrategyConfig

from execsys.config import OrderManagerSettings
from execsys.config import Settings
from execsys.contracts import TOPIC_ALERT
from execsys.contracts import TOPIC_ORDER_ISSUE
from execsys.contracts import TOPIC_RISK_STATE
from execsys.contracts import TOPIC_SIGNAL_ALL
from execsys.contracts import Alert
from execsys.contracts import OrderIssue
from execsys.contracts import RiskState
from execsys.contracts import RiskStateChanged
from execsys.contracts import Severity
from execsys.contracts import Signal
from execsys.order_manager.book import ZERO
from execsys.order_manager.book import SignalBook
from execsys.order_manager.book import VirtualBook
from execsys.order_manager.book import plan_order
from execsys.persistence.state_store import JsonStateStore
from execsys.telemetry import get_metrics
from execsys.telemetry import get_order_tracer


IB_CLIENT = ClientId.from_str("IB")


class OrderManager(Strategy):
    def __init__(self, settings: Settings) -> None:
        om: OrderManagerSettings = settings.order_manager
        super().__init__(
            StrategyConfig(
                strategy_id=StrategyId.from_str(om.strategy_id),
                order_id_tag=om.order_id_tag,
            ),
        )
        self.om = om
        self.settings = settings
        self.sizes: dict[str, Decimal] = {s.strategy_id: s.size for s in settings.enabled_strategies}
        self.allowed: dict[str, set[str]] = {s.strategy_id: set(s.instruments) for s in settings.enabled_strategies}
        self.signals = SignalBook()
        self.virtual = VirtualBook()
        self.risk_state = RiskState.ACTIVE
        self.store = JsonStateStore(om.state_file)
        self._last_submit_ns: dict[str, int] = {}
        self._submit_ts: dict[str, int] = {}
        self._metrics = get_metrics()
        self._tracer = get_order_tracer()
        self.client_id: ClientId | None = IB_CLIENT

    # -- lifecycle -------------------------------------------------------------------------

    def on_start(self) -> None:
        state = self.store.load()
        if state:
            self.signals = SignalBook.from_dict(state.get("signals", {}))
            self.virtual = VirtualBook.from_dict(state.get("virtual", {}))
            self.log.info(f"Restored state for {len(self.signals.instruments())} instruments")
        self.store.start()
        self.subscribe_topic(TOPIC_SIGNAL_ALL, self._on_signal_message)
        # Higher priority so risk changes apply before any signal published in the same cycle
        self.subscribe_topic(TOPIC_RISK_STATE, self._on_risk_state, priority=10)
        for iid in self.settings.universe:
            instrument = self.cache.instrument(InstrumentId.from_str(iid))
            if instrument is not None:
                self.on_instrument(instrument)
            else:
                self.request_instrument(InstrumentId.from_str(iid), client_id=self.client_id)

    def on_instrument(self, instrument: Any) -> None:
        # Quotes are needed for LIMIT pricing and for unrealised PnL marks
        if str(instrument.id) in self.settings.universe:
            self.subscribe_quotes(instrument.id, client_id=self.client_id)

    def on_stop(self) -> None:
        for iid in self.settings.universe:
            instrument_id = InstrumentId.from_str(iid)
            self.cancel_all_orders(instrument_id)
            if self.om.flatten_on_stop:
                self.close_all_positions(instrument_id)
        self._save()
        self.store.stop()

    # -- inputs ----------------------------------------------------------------------------

    def _on_signal_message(self, message: object) -> None:
        if not isinstance(message, Signal):
            return
        try:
            self._handle_signal(message)
        except Exception as e:
            self._metrics.handler_errors.add(1, {"strategy_id": self.om.strategy_id})
            self.log.error(f"signal handling failed {message}: {type(e).__name__}: {e}")

    def _handle_signal(self, signal: Signal) -> None:
        size = self.sizes.get(signal.strategy_id)
        if size is None or signal.instrument_id not in self.allowed.get(signal.strategy_id, ()):
            self.log.warning(f"Ignoring signal from unknown strategy/instrument: {signal}")
            return
        age_s = (self.clock.timestamp_ns() - signal.ts_init) / 1e9
        if age_s > self.om.signal_stale_secs:
            self.log.warning(f"Ignoring stale signal ({age_s:.0f}s old): {signal}")
            return
        if not self.signals.update(signal, size):
            return
        self._save()
        self._rebalance(signal.instrument_id, signal_ts=signal.ts_init)

    def _on_risk_state(self, message: object) -> None:
        if not isinstance(message, RiskStateChanged):
            return
        previous, self.risk_state = self.risk_state, message.state
        self.log.warning(f"Risk state {previous} -> {message.state}: {', '.join(message.reasons)}")
        if message.state is not RiskState.ACTIVE:
            # Pull working orders that could add risk; plan_order re-sends only reducing ones
            for iid in self.settings.universe:
                self.cancel_all_orders(InstrumentId.from_str(iid))
        if message.flatten:
            for iid in self.settings.universe:
                self.close_all_positions(InstrumentId.from_str(iid))
        elif message.state is RiskState.REDUCING or (
            message.state is RiskState.ACTIVE and previous is not RiskState.ACTIVE
        ):
            for iid in self.signals.instruments():
                self._rebalance(iid)

    # -- core ------------------------------------------------------------------------------

    def _rebalance(self, iid: str, signal_ts: int = 0) -> None:
        instrument_id = InstrumentId.from_str(iid)
        instrument = self.cache.instrument(instrument_id)
        if instrument is None:
            self.log.warning(f"No instrument loaded for {iid}; cannot rebalance")
            return

        now = self.clock.timestamp_ns()
        last = self._last_submit_ns.get(iid, 0)
        if now - last < self.om.min_order_interval_ms * 1_000_000:
            # Throttled: re-evaluate when the throttle window ends
            self.clock.set_time_alert_ns(
                f"om.retry.{iid}",
                last + self.om.min_order_interval_ms * 1_000_000,
                callback=lambda _e, iid=iid: self._rebalance(iid),
                allow_past=True,
            )
            return

        position = Decimal(self.portfolio.net_position(instrument_id))
        working = self._working_qty(instrument_id)
        plan = plan_order(
            target=self.signals.target(iid),
            position=position,
            working=working,
            max_position=self.settings.max_position(iid),
            risk_state=self.risk_state,
        )
        if plan.quantity == 0:
            if plan.reason not in ("at target", "working order pending"):
                self.log.info(f"{iid}: no order ({plan.reason})")
            return

        side = OrderSide.BUY if plan.quantity > 0 else OrderSide.SELL
        quantity = instrument.make_qty(float(abs(plan.quantity)), round_down=True)
        if Decimal(str(quantity)) == 0:
            return
        reduce_only = self.risk_state is RiskState.REDUCING
        order = self._build_order(instrument, side, quantity, reduce_only)
        if order is None:
            return

        coid = str(order.client_order_id)
        self._last_submit_ns[iid] = now
        self._submit_ts[coid] = now
        if signal_ts:
            self._metrics.signal_to_submit_ms.record((now - signal_ts) / 1e6, {"instrument_id": iid})
        self._tracer.start(
            coid,
            {
                "instrument_id": iid,
                "side": str(side),
                "quantity": str(quantity),
                "target": str(self.signals.target(iid)),
                "position": str(position),
                "risk_state": str(self.risk_state),
            },
        )
        self._metrics.orders_submitted.add(1, {"instrument_id": iid, "side": str(side)})
        self.log.info(f"SUBMIT {side} {quantity} {iid} (target={self.signals.target(iid)} position={position})")
        self.submit_order(order, client_id=self.client_id)

    def _build_order(self, instrument: Any, side: OrderSide, quantity: Any, reduce_only: bool) -> Any:
        if self.om.order_type == "MARKET":
            return self.order_factory.market(
                instrument_id=instrument.id,
                order_side=side,
                quantity=quantity,
                time_in_force=TimeInForce.DAY,
                reduce_only=reduce_only,
            )
        quote = self.cache.quote(instrument.id)
        if quote is None:
            self.log.warning(f"No quote for {instrument.id}; cannot price LIMIT order")
            return None
        tick = Decimal(str(instrument.price_increment)) * self.om.limit_offset_ticks
        touch = Decimal(str(quote.ask_price if side == OrderSide.BUY else quote.bid_price))
        px = touch + tick if side == OrderSide.BUY else touch - tick
        return self.order_factory.limit(
            instrument_id=instrument.id,
            order_side=side,
            quantity=quantity,
            price=instrument.make_price(float(px)),
            time_in_force=TimeInForce.DAY,
            reduce_only=reduce_only,
        )

    def _working_qty(self, instrument_id: InstrumentId) -> Decimal:
        total = ZERO
        for order in self.cache.orders_open(instrument_id=instrument_id, strategy_id=self.strategy_id):
            leaves = Decimal(str(order.leaves_qty))
            total += leaves if order.is_buy else -leaves
        for order in self.cache.orders_inflight(instrument_id=instrument_id, strategy_id=self.strategy_id):
            leaves = Decimal(str(order.leaves_qty))
            total += leaves if order.is_buy else -leaves
        return total

    # -- order events ----------------------------------------------------------------------

    def on_order_filled(self, event: Any) -> None:
        iid = str(event.instrument_id)
        coid = str(event.client_order_id)
        qty = Decimal(str(event.last_qty)) * (1 if event.is_buy else -1)
        px = Decimal(str(event.last_px))
        instrument = self.cache.instrument(event.instrument_id)
        multiplier = Decimal(str(instrument.multiplier)) if instrument is not None else Decimal(1)
        allocation = self.virtual.allocate_fill(iid, qty, px, self.signals.strategy_targets(iid), multiplier)

        now = self.clock.timestamp_ns()
        submit_ts = self._submit_ts.get(coid)
        if submit_ts:
            self._metrics.submit_to_fill_ms.record((now - submit_ts) / 1e6, {"instrument_id": iid})
        self._metrics.fills.add(1, {"instrument_id": iid})
        self._tracer.event(coid, "fill", {"qty": str(qty), "px": str(px)})
        self.log.info(f"FILL {iid} {qty}@{px} allocation={ {k: str(v) for k, v in allocation.items()} }")

        order = self.cache.order(event.client_order_id)
        if order is None or order.is_closed:
            self._finish(coid, "filled")
        self._save()
        self._rebalance(iid)

    def on_order_canceled(self, event: Any) -> None:
        self._finish(str(event.client_order_id), "canceled")
        self._rebalance(str(event.instrument_id))

    def on_order_expired(self, event: Any) -> None:
        self._finish(str(event.client_order_id), "expired")
        self._rebalance(str(event.instrument_id))

    def on_order_denied(self, event: Any) -> None:
        self._metrics.orders_denied.add(1, {"instrument_id": str(event.instrument_id)})
        self._finish(str(event.client_order_id), f"denied: {event.reason}", error=True)
        self._issue("denied", event)

    def on_order_rejected(self, event: Any) -> None:
        self._metrics.orders_rejected.add(1, {"instrument_id": str(event.instrument_id)})
        self._finish(str(event.client_order_id), f"rejected: {event.reason}", error=True)
        self._issue("rejected", event)

    def _finish(self, coid: str, outcome: str, error: bool = False) -> None:
        self._tracer.end(coid, outcome, error)
        self._submit_ts.pop(coid, None)

    # -- helpers ---------------------------------------------------------------------------

    def _issue(self, kind: str, event: Any) -> None:
        now = self.clock.timestamp_ns()
        self.publish_message(TOPIC_ORDER_ISSUE, OrderIssue(kind, str(event.instrument_id), str(event.reason), now))
        self._alert(Severity.WARNING, f"Order {kind} {event.instrument_id}: {event.reason}")

    def _alert(self, severity: Severity, message: str) -> None:
        self.publish_message(TOPIC_ALERT, Alert(severity, "order_manager", message, self.clock.timestamp_ns()))

    def _save(self) -> None:
        self.store.save({"signals": self.signals.to_dict(), "virtual": self.virtual.to_dict()})

    def strategy_pnl(self) -> dict[str, dict[str, Decimal]]:
        """
        Per-strategy realised and unrealised PnL from the virtual book (for monitoring).
        """
        out: dict[str, dict[str, Decimal]] = {}
        for iid in self.signals.instruments():
            instrument_id = InstrumentId.from_str(iid)
            quote = self.cache.quote(instrument_id)
            instrument = self.cache.instrument(instrument_id)
            mult = Decimal(str(instrument.multiplier)) if instrument is not None else Decimal(1)
            mark = (
                (Decimal(str(quote.bid_price)) + Decimal(str(quote.ask_price))) / 2 if quote is not None else None
            )
            for sid, pos in self.virtual.positions(iid).items():
                agg = out.setdefault(sid, {"realized": ZERO, "unrealized": ZERO})
                agg["realized"] += pos.realized_pnl
                if mark is not None:
                    agg["unrealized"] += pos.unrealized_pnl(mark, mult)
        return out
