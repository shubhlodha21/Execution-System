# Adding a strategy

A strategy turns market data into **target directions** (+1 long, −1 short, 0 flat). It never places orders.

## 1. Write the class

`execsys/strategies/my_strategy.py`:

```python
from typing import Any

from nautilus_trader.model import Bar, BarType

from execsys.config import StrategySettings
from execsys.strategies.base import SignalStrategy


class MySignal(SignalStrategy):
    def __init__(self, settings: StrategySettings) -> None:
        super().__init__(settings)
        self.bar_spec = str(self.params.get("bar_spec", "1-MINUTE-LAST-EXTERNAL"))
        self.state = {iid: ... for iid in self.instrument_ids}   # per-ticker state, O(1) updates

    def on_start(self) -> None:
        self.load_instruments()            # -> on_instrument for each configured instrument

    def on_instrument(self, instrument: Any) -> None:
        bar_type = BarType.from_str(f"{instrument.id}-{self.bar_spec}")
        self.subscribe_bars(bar_type, client_id=self.client_id)

    def on_bar(self, bar: Bar) -> None:
        self.guard(bar.bar_type.instrument_id, self._update, bar)   # isolates per-ticker errors

    def _update(self, bar: Bar) -> None:
        direction = ...                    # -1, 0 or +1
        self.emit(bar.bar_type.instrument_id, direction, ts_event=bar.ts_event, reason="why")
```

Rules: keep handlers fast (no I/O, no pandas), keep per-ticker state in dicts, put pure maths in
small functions you can unit-test (like `crossover` in `ema_cross.py`).

## 2. Register it in `config/settings.yaml`

```yaml
strategies:
  - strategy_id: MY-SIGNAL-001        # unique, contains "-"
    class_path: execsys.strategies.my_strategy:MySignal
    enabled: true
    instruments: [AAPL.XNAS]
    size: "5"
    params: {bar_spec: 5-SECOND-LAST-EXTERNAL}
```

No other code changes. The Order Manager nets it with the other strategies, and the Risk Monitor
and Recorder pick up its instruments through `settings.universe`.

## 3. Test it

- Unit-test the pure signal function.
- Add a scenario to `tests/test_backtest_flow.py` (synthetic bars → expected fills) if it changes wiring.
- Run `--data-only` against paper and compare logged `SIGNAL` lines with the TWS chart before trading it.
