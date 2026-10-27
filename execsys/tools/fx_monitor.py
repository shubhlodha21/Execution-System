"""
Live FX monitor in the terminal: quote table + cross-rate matrix, refreshed every second.

    python -m execsys.tools.fx_monitor                              # EUR/USD, USD/CHF, USD/JPY
    python -m execsys.tools.fx_monitor --pairs EUR/USD GBP/USD USD/JPY
    python -m execsys.tools.fx_monitor --port 7497                  # TWS paper instead of Gateway

Runs its own data-only node with the configured IB client_id (21 by default). IB allows one connection
per client_id, so while the trading app is running pass another one, e.g. --client-id 22.
IB quotes CHF as USD/CHF; CHF->USD appears in the matrix.
Printing from the timer is intentional: this is a viewing tool, not part of the trading path.
"""

from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import dataclass
from typing import Any

from nautilus_trader.common import DataActor
from nautilus_trader.config import DataActorConfig
from nautilus_trader.model import ActorId
from nautilus_trader.model import ClientId
from nautilus_trader.model import InstrumentId

from execsys.app import build_node
from execsys.config import load_settings
from execsys.tools.fx_matrix import FxQuote
from execsys.tools.fx_matrix import render


IB_CLIENT = ClientId.from_str("IB")
DEFAULT_PAIRS = ["EUR/USD", "USD/CHF", "USD/JPY"]
_CLEAR = "\x1b[H\x1b[2J"


@dataclass(frozen=True)
class FxMonitorOptions:
    pairs: list[str]  # normalized ids, e.g. "EUR/USD.IDEALPRO"
    refresh_secs: float = 1.0


class FxMonitor(DataActor):
    # Nautilus DataActor constructors take a single argument, hence the options object
    def __init__(self, options: FxMonitorOptions) -> None:
        super().__init__(DataActorConfig(actor_id=ActorId.from_str("FX-MONITOR")))
        self.pairs = options.pairs
        self.instrument_ids = [InstrumentId.from_str(p) for p in options.pairs]
        self.refresh = dt.timedelta(seconds=options.refresh_secs)
        self.quotes: dict[str, FxQuote] = {}
        self._subscribed: set[InstrumentId] = set()

    def on_start(self) -> None:
        for iid in self.instrument_ids:
            instrument = self.cache.instrument(iid)
            if instrument is not None:
                self.on_instrument(instrument)
            else:
                self.request_instrument(iid, client_id=IB_CLIENT)
        self.clock.set_timer("fx_monitor.render", self.refresh, callback=self._render, fire_immediately=True)

    def on_stop(self) -> None:
        self.clock.cancel_timer("fx_monitor.render")

    def on_instrument(self, instrument: Any) -> None:
        if instrument.id in self.instrument_ids and instrument.id not in self._subscribed:
            self._subscribed.add(instrument.id)
            self.subscribe_quotes(instrument.id, client_id=IB_CLIENT)

    def on_quote(self, quote: Any) -> None:
        self.quotes[str(quote.instrument_id)] = FxQuote(
            float(quote.bid_price),
            float(quote.ask_price),
            quote.ts_init,
        )

    def _render(self, _event: Any) -> None:
        print(_CLEAR + render(self.quotes, self.pairs, self.clock.timestamp_ns()), flush=True)


def normalize_pair(pair: str) -> str:
    """
    "EURUSD" / "EUR/USD" / "EUR/USD.IDEALPRO" -> "EUR/USD.IDEALPRO"
    """
    p = pair.upper().strip()
    if "." in p:
        return p
    if "/" not in p and len(p) == 6:
        p = f"{p[:3]}/{p[3:]}"
    return f"{p}.IDEALPRO"


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m execsys.tools.fx_monitor", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pairs", nargs="+", default=DEFAULT_PAIRS, help="e.g. EUR/USD USDJPY GBP/USD")
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--port", type=int, help="override ib.port (7497 TWS paper, 4002 Gateway paper)")
    parser.add_argument("--client-id", type=int, help="IB client_id for this tool (default: ib.client_id, 21); use another while the trading app runs")
    parser.add_argument("--refresh", type=float, default=1.0, help="seconds between screen refreshes")
    args = parser.parse_args()

    settings = load_settings(args.config)
    pairs = [normalize_pair(p) for p in args.pairs]
    ib = settings.ib.model_copy(
        update={
            "client_id": args.client_id or settings.ib.client_id,
            **({"port": args.port} if args.port else {}),
        },
    )
    node_settings = settings.node.model_copy(update={"name": "FX-MONITOR", "log_level": "WARNING", "reconciliation": False})
    settings = settings.model_copy(update={"ib": ib, "node": node_settings})

    print(f"Connecting to IB {ib.host}:{ib.port} as client_id {ib.client_id} for {', '.join(pairs)} ...", flush=True)
    node = build_node(settings, data_only=True, instruments=pairs)
    node.add_actor(FxMonitor(FxMonitorOptions(pairs, args.refresh)))
    try:
        node.run()
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
