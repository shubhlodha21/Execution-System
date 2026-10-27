"""
Base class for alpha strategies.

A strategy is a DataActor, not a Nautilus Strategy: it has no order API at all. It reads market
data and publishes `Signal` messages; the Order Manager is the only component that trades.
"""

from __future__ import annotations

from typing import Any

from nautilus_trader.common import DataActor
from nautilus_trader.config import DataActorConfig
from nautilus_trader.model import ActorId
from nautilus_trader.model import ClientId
from nautilus_trader.model import InstrumentId

from execsys.config import StrategySettings
from execsys.contracts import Signal
from execsys.contracts import signal_topic
from execsys.telemetry import get_metrics


IB_CLIENT = ClientId.from_str("IB")


class SignalStrategy(DataActor):
    """
    Subclasses implement `on_start` (subscribe) and data handlers, and call `emit()`.

    Handlers should wrap per-instrument work in `self.guard(...)` so one bad ticker logs an
    error instead of stopping the strategy.
    """

    def __init__(self, settings: StrategySettings) -> None:
        super().__init__(DataActorConfig(actor_id=ActorId.from_str(settings.strategy_id)))
        self.settings = settings
        self.strategy_id = settings.strategy_id
        self.instrument_ids = [InstrumentId.from_str(i) for i in settings.instruments]
        self.params: dict[str, Any] = dict(settings.params)
        self._topic = signal_topic(settings.strategy_id)
        self._last_direction: dict[str, int] = {}
        self._metrics = get_metrics()
        self.client_id: ClientId | None = IB_CLIENT  # data client to route requests to

    def load_instruments(self) -> None:
        """
        Call from on_start: uses instruments already in the cache (loaded by the IB instrument
        provider at connect) and requests only the missing ones. Each ends up in on_instrument.
        """
        for instrument_id in self.instrument_ids:
            instrument = self.cache.instrument(instrument_id)
            if instrument is not None:
                self.on_instrument(instrument)
            else:
                self.request_instrument(instrument_id, client_id=self.client_id)

    def emit(
        self,
        instrument_id: InstrumentId,
        direction: int,
        ts_event: int,
        strength: float = 1.0,
        reason: str = "",
    ) -> None:
        """
        Publish the strategy's target direction for an instrument. Repeating the current
        direction is suppressed here, so callers can emit on every bar.
        """
        key = str(instrument_id)
        if self._last_direction.get(key) == direction:
            return
        self._last_direction[key] = direction

        now = self.clock.timestamp_ns()
        signal = Signal(
            strategy_id=self.strategy_id,
            instrument_id=key,
            direction=direction,
            strength=strength,
            reason=reason,
            ts_event=ts_event,
            ts_init=now,
        )
        attrs = {"strategy_id": self.strategy_id, "instrument_id": key}
        self._metrics.signals.add(1, attrs)
        if ts_event:
            self._metrics.data_to_signal_ms.record((now - ts_event) / 1e6, attrs)
        self.log.info(f"SIGNAL {key} direction={direction} strength={strength:.2f} {reason}")
        self.publish_message(self._topic, signal)

    def guard(self, instrument_id: object, fn: Any, *args: Any) -> None:
        try:
            fn(*args)
        except Exception as e:
            self._metrics.handler_errors.add(1, {"strategy_id": self.strategy_id, "instrument_id": str(instrument_id)})
            self.log.error(f"{self.strategy_id} {instrument_id}: {type(e).__name__}: {e}")
