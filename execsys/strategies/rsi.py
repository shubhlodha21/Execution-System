"""
RSI overbought / oversold signal strategy (mean reversion).

Long when RSI falls to `oversold` or below, short when it rises to `overbought` or above. With
`exit_level` set (default 50), a long goes flat once RSI recovers to that level and a short goes
flat once it falls back to it; with `exit_level: null` a position is held until the opposite
extreme. Indicators warm up from historical bars; signals are emitted only from live bars.

Works for equities (`bar_spec: 5-MINUTE-LAST-EXTERNAL`) and FX (`bar_spec: 5-MINUTE-MID-EXTERNAL`,
IDEALPRO has no trades).
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Any

from nautilus_trader.indicators import RelativeStrengthIndex
from nautilus_trader.model import Bar
from nautilus_trader.model import BarType
from nautilus_trader.model import InstrumentId

from execsys.config import StrategySettings
from execsys.strategies.base import SignalStrategy
from execsys.strategies.base import warmup_window


@dataclass
class _TickerState:
    bar_type: BarType
    rsi: RelativeStrengthIndex
    last_ts: int = 0
    live: bool = field(default=False)


def rsi_signal(
    rsi: float,
    current: int,
    overbought: float,
    oversold: float,
    exit_level: float | None,
) -> int | None:
    """
    New target direction for an RSI reading, or None to keep `current`.
    """
    if rsi <= oversold:
        return 1
    if rsi >= overbought:
        return -1
    if exit_level is not None:
        if current == 1 and rsi >= exit_level:
            return 0
        if current == -1 and rsi <= exit_level:
            return 0
    return None


class RsiSignal(SignalStrategy):
    def __init__(self, settings: StrategySettings) -> None:
        super().__init__(settings)
        self.bar_spec = str(self.params.get("bar_spec", "5-MINUTE-LAST-EXTERNAL"))
        self.period = int(self.params.get("period", 14))
        self.overbought = float(self.params.get("overbought", 70))
        self.oversold = float(self.params.get("oversold", 30))
        exit_level = self.params.get("exit_level", 50)
        self.exit_level = None if exit_level is None else float(exit_level)
        self.warmup_days = int(self.params.get("warmup_days", 3))
        if not 0 < self.oversold < self.overbought < 100:
            raise ValueError("RSI levels must satisfy 0 < oversold < overbought < 100")
        if self.exit_level is not None and not self.oversold < self.exit_level < self.overbought:
            raise ValueError("exit_level must lie between oversold and overbought")
        self.states: dict[InstrumentId, _TickerState] = {
            iid: _TickerState(
                bar_type=BarType.from_str(f"{iid}-{self.bar_spec}"),
                rsi=RelativeStrengthIndex(self.period),
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
            start, end = warmup_window(self.clock.utc_now(), self.warmup_days)
            self.request_bars(state.bar_type, start=start, end=end, client_id=self.client_id)
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
        state.rsi.handle_bar(bar)
        if not state.rsi.initialized or not live:
            return

        rsi = state.rsi.value
        current = self._last_direction.get(str(instrument_id), 0)
        direction = rsi_signal(rsi, current, self.overbought, self.oversold, self.exit_level)
        if direction is None:
            return
        if direction == 1:
            strength = min(1.0, (self.oversold - rsi) / self.oversold + 0.5)
        elif direction == -1:
            strength = min(1.0, (rsi - self.overbought) / (100 - self.overbought) + 0.5)
        else:
            strength = 0.0
        self.emit(
            instrument_id,
            direction,
            ts_event=bar.ts_event,
            strength=strength,
            reason=f"rsi{self.period}={rsi:.1f} (ob {self.overbought:g} / os {self.oversold:g}) close={bar.close}",
        )
