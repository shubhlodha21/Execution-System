"""
Data Recorder: copies quotes, trades and bars from the bus to storage without blocking.

Handlers convert each Nautilus object to a tuple of strings (exact prices, never floats) and
enqueue it; `BatchWriter` does all I/O on its own thread.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from nautilus_trader.common import DataActor
from nautilus_trader.config import DataActorConfig
from nautilus_trader.model import ActorId
from nautilus_trader.model import Bar
from nautilus_trader.model import BarType
from nautilus_trader.model import ClientId
from nautilus_trader.model import InstrumentId
from nautilus_trader.model import QuoteTick
from nautilus_trader.model import TradeTick

from execsys.config import Settings
from execsys.persistence.writer import BatchWriter
from execsys.persistence.writer import CsvSink
from execsys.telemetry import get_metrics


IB_CLIENT = ClientId.from_str("IB")

_ASSET_CLASS = {
    "Equity": "EQUITY",
    "CurrencyPair": "FX",
    "Cfd": "CFD",
    "FuturesContract": "FUTURE",
    "OptionContract": "OPTION",
    "IndexInstrument": "INDEX",
    "CryptoPerpetual": "CRYPTO",
    "CryptoFuture": "CRYPTO",
}


def bar_spec_for(asset_class: str, bar_spec: str) -> str:
    """
    FX has no trade prices on IDEALPRO, so LAST bars become MID bars (e.g. 1-MINUTE-MID-EXTERNAL).
    """
    return bar_spec.replace("-LAST-", "-MID-") if asset_class == "FX" else bar_spec


def _utc(ns: int) -> str:
    return dt.datetime.fromtimestamp(ns / 1e9, dt.UTC).isoformat(timespec="microseconds")


class DataRecorder(DataActor):
    def __init__(self, settings: Settings) -> None:
        super().__init__(DataActorConfig(actor_id=ActorId.from_str("DATA-RECORDER-001")))
        self.rs = settings.recorder
        self.instrument_ids = [InstrumentId.from_str(i) for i in settings.universe]
        self.writer = BatchWriter(
            CsvSink(self.rs.directory),
            queue_size=self.rs.queue_size,
            batch_size=self.rs.batch_size,
            flush_interval=self.rs.flush_interval_secs,
        )
        self._asset_class: dict[InstrumentId, str] = {}
        self.client_id: ClientId | None = IB_CLIENT
        m = get_metrics()
        self.writer.on_dropped = lambda n: m.recorder_dropped.add(n)
        self.writer.on_written = lambda n: m.recorder_written.add(n)
        m.register_gauge("recorder_queue_depth", lambda: [(float(self.writer.depth()), {})])

    def on_start(self) -> None:
        self.writer.start()
        for iid in self.instrument_ids:
            instrument = self.cache.instrument(iid)
            if instrument is not None:
                self.on_instrument(instrument)
            else:
                self.request_instrument(iid, client_id=self.client_id)

    def on_instrument(self, instrument: Any) -> None:
        iid = instrument.id
        if iid not in self.instrument_ids or iid in self._asset_class:
            return
        asset_class = _ASSET_CLASS.get(type(instrument).__name__, type(instrument).__name__.upper())
        self._asset_class[iid] = asset_class
        if self.rs.record_quotes:
            self.subscribe_quotes(iid, client_id=self.client_id)
        if self.rs.record_trades and asset_class != "FX":  # IDEALPRO FX has no trades
            self.subscribe_trades(iid, client_id=self.client_id)
        if self.rs.record_bars:
            self.subscribe_bars(BarType.from_str(f"{iid}-{bar_spec_for(asset_class, self.rs.bar_spec)}"), client_id=self.client_id)

    def on_stop(self) -> None:
        self.writer.stop()

    def on_quote(self, quote: QuoteTick) -> None:
        mid = (quote.bid_price.as_decimal() + quote.ask_price.as_decimal()) / 2
        self.writer.put(
            (
                "tick",
                "QUOTE",
                self._asset_class.get(quote.instrument_id, ""),
                str(quote.instrument_id),
                _utc(quote.ts_event),
                str(quote.ts_init),
                str(mid),
                "",
                "",
                "",
            ),
        )

    def on_trade(self, trade: TradeTick) -> None:
        self.writer.put(
            (
                "tick",
                "TRADE",
                self._asset_class.get(trade.instrument_id, ""),
                str(trade.instrument_id),
                _utc(trade.ts_event),
                str(trade.ts_init),
                "",
                str(trade.price),
                str(trade.size),
                str(trade.aggressor_side).split(".")[-1],
            ),
        )

    def on_bar(self, bar: Bar) -> None:
        iid = bar.bar_type.instrument_id
        self.writer.put(
            (
                "bar",
                str(bar.bar_type),
                self._asset_class.get(iid, ""),
                str(iid),
                _utc(bar.ts_event),
                str(bar.ts_init),
                str(bar.open),
                str(bar.high),
                str(bar.low),
                str(bar.close),
                str(bar.volume),
            ),
        )
