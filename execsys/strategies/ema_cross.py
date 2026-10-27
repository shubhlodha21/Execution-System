"""
EMA crossover signal strategy (ported from examples/live/interactive_brokers/ib_multi_ticker_signals.py).

Long when fast EMA crosses above slow EMA, short when it crosses below. Indicators warm up
from historical bars; signals are emitted only from live bars.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from dataclasses import field
from typing import Any

from nautilus_trader.indicators import ExponentialMovingAverage
from nautilus_trader.model import Bar
from nautilus_trader.model import BarType
from nautilus_trader.model import InstrumentId

from execsys.config import StrategySettings
from execsys.strategies.base import SignalStrategy


@dataclass
class _TickerState:
    bar_type: BarType
    fast: ExponentialMovingAverage
    slow: ExponentialMovingAverage
    last_ts: int = 0
    prev_diff: float | None = None
    live: bool = field(default=False)


def crossover(prev_diff: float | None, diff: float) -> int | None:
    """
    +1 on an upward cross, -1 on a downward cross, None otherwise.
    """
    if prev_diff is None:
        return None
    if prev_diff <= 0 < diff:
        return 1
    if prev_diff >= 0 > diff:
        return -1
    return None


class EmaCrossSignal(SignalStrategy):
    def __init__(self, settings: StrategySettings) -> None:
        super().__init__(settings)
        self.bar_spec = str(self.params.get("bar_spec", "1-MINUTE-LAST-EXTERNAL"))
        self.fast_period = int(self.params.get("fast_period", 10))
        self.slow_period = int(self.params.get("slow_period", 30))
        self.warmup_days = int(self.params.get("warmup_days", 3))
        if self.fast_period >= self.slow_period:
            raise ValueError("fast_period must be < slow_period")
        self.states: dict[InstrumentId, _TickerState] = {
            iid: _TickerState(
                bar_type=BarType.from_str(f"{iid}-{self.bar_spec}"),
                fast=ExponentialMovingAverage(self.fast_period),
                slow=ExponentialMovingAverage(self.slow_period),
            )
            for iid in self.instrument_ids
        }

    def on_start(self) -> None:
        self.load_instruments()

    def on_instrument(self, instrument: Any) -> None:
        state = self.states.get(instrument.id)
        if state is None or state.live:
            return
        state.live = True
        if self.warmup_days > 0:
            start = dt.datetime.now(dt.UTC) - dt.timedelta(days=self.warmup_days)
            self.request_bars(state.bar_type, start=start, client_id=self.client_id)
        self.subscribe_bars(state.bar_type, client_id=self.client_id)

    def on_historical_bars(self, bars: Any) -> None:
        for bar in bars:
            self.guard(bar.bar_type.instrument_id, self._update, bar, False)

    def on_bar(self, bar: Bar) -> None:
        self.guard(bar.bar_type.instrument_id, self._update, bar, True)

    def _update(self, bar: Bar, live: bool) -> None:
        instrument_id = bar.bar_type.instrument_id
        state = self.states.get(instrument_id)
        # Warm-up and live streams can overlap, so process each bar once per ticker
        if state is None or bar.ts_event <= state.last_ts:
            return
        state.last_ts = bar.ts_event
        state.fast.handle_bar(bar)
        state.slow.handle_bar(bar)
        if not (state.fast.initialized and state.slow.initialized):
            return

        diff = state.fast.value - state.slow.value
        direction = crossover(state.prev_diff, diff)
        state.prev_diff = diff
        if direction is not None and live:
            self.emit(
                instrument_id,
                direction,
                ts_event=bar.ts_event,
                strength=1.0,
                reason=f"ema{self.fast_period}/{self.slow_period} cross close={bar.close}",
            )
