"""
TradeJournal: day- and ticker-wise P&L files from IB executions.

    data/pnl/<YYYY-MM-DD>/<TICKER>.csv   one row per IB execution (price, qty, IB commission, position,
                                         average cost, realised P&L, running day P&L, strategies, IB ids)
    data/pnl/<YYYY-MM-DD>/summary.csv    one row per ticker for the day, rewritten after every fill

Fills come from the Order Manager on `app.fill` (IB's execution id, price, quantity and commission).
At startup open positions are seeded from the IB-reconciled positions in the cache. All file I/O runs
on the journal's writer thread; the event thread only updates the in-memory ledger and enqueues.
Days are UTC dates (one US session never crosses a UTC day).
"""

from __future__ import annotations

import csv
import datetime as dt
import logging
import os
import queue
import threading
from decimal import Decimal
from pathlib import Path
from typing import Any

from nautilus_trader.common import DataActor
from nautilus_trader.config import DataActorConfig
from nautilus_trader.model import ActorId

from execsys.config import Settings
from execsys.contracts import TOPIC_FILL
from execsys.contracts import TradeFill
from execsys.journal.ledger import FILL_FIELDS
from execsys.journal.ledger import SUMMARY_FIELDS
from execsys.journal.ledger import Ledger
from execsys.persistence.state_store import JsonStateStore
from execsys.tools.fx_matrix import IST


_log = logging.getLogger("execsys.journal")


def ticker_file_name(instrument_id: str) -> str:
    """
    "ADI.XNAS" -> "ADI.csv", "EUR/USD.IDEALPRO" -> "EUR-USD.csv"
    """
    return instrument_id.split(".", 1)[0].replace("/", "-") + ".csv"


class JournalWriter:
    """
    Background writer: appends fill rows and atomically rewrites summary files.
    """

    def __init__(self) -> None:
        self._q: queue.Queue[tuple[str, Path, tuple[str, ...], list[tuple[str, ...]]] | None] = queue.Queue()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="journal-writer", daemon=True)
            self._thread.start()

    def append(self, path: Path, header: tuple[str, ...], row: tuple[str, ...]) -> None:
        self._q.put(("append", path, header, [row]))

    def rewrite(self, path: Path, header: tuple[str, ...], rows: list[tuple[str, ...]]) -> None:
        self._q.put(("rewrite", path, header, rows))

    def stop(self) -> None:
        if self._thread is not None:
            self._q.put(None)
            self._thread.join(timeout=10)
            self._thread = None

    def _run(self) -> None:
        while (item := self._q.get()) is not None:
            kind, path, header, rows = item
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                if kind == "append":
                    new = not path.exists()
                    with path.open("a", newline="", encoding="utf-8") as f:
                        w = csv.writer(f)
                        if new:
                            w.writerow(header)
                        w.writerows(rows)
                else:
                    tmp = path.with_suffix(path.suffix + ".tmp")
                    with tmp.open("w", newline="", encoding="utf-8") as f:
                        w = csv.writer(f)
                        w.writerow(header)
                        w.writerows(rows)
                    os.replace(tmp, path)
            except OSError:
                _log.exception("journal write failed for %s", path)


class TradeJournal(DataActor):
    def __init__(self, settings: Settings) -> None:
        super().__init__(DataActorConfig(actor_id=ActorId.from_str("TRADE-JOURNAL-001")))
        self.directory = Path(settings.journal.directory)
        self.store = JsonStateStore(settings.journal.state_file)
        self.writer = JournalWriter()
        self.ledger = Ledger()

    def on_start(self) -> None:
        self.ledger = Ledger.from_dict(self.store.load())
        self._seed_from_ib()
        self.store.start()
        self.writer.start()
        self.subscribe_topic(TOPIC_FILL, self._on_fill)

    def on_stop(self) -> None:
        self._save()
        self.store.stop()
        self.writer.stop()

    def _seed_from_ib(self) -> None:
        """
        Positions reconciled from IB are the truth for quantity and average cost at startup.
        """
        for pos in self.cache.positions_open():
            ticker = str(pos.instrument_id)
            qty = Decimal(str(pos.quantity)) * (1 if pos.signed_qty > 0 else -1)
            held = self.ledger.positions.get(ticker)
            if held is None or held.quantity != qty:
                self.ledger.seed(ticker, qty, Decimal(repr(pos.avg_px_open)))
                self.log.info(f"journal: {ticker} seeded from IB position {qty} @ {pos.avg_px_open}")
        open_ids = {str(p.instrument_id) for p in self.cache.positions_open()}
        for ticker, held in list(self.ledger.positions.items()):
            if held.quantity != 0 and ticker not in open_ids:
                self.ledger.seed(ticker, Decimal(0), Decimal(0))  # IB shows flat

    def _on_fill(self, message: object) -> None:
        if not isinstance(message, TradeFill):
            return
        try:
            self._record(message)
        except Exception as e:  # the journal must never disturb trading
            self.log.error(f"journal failed for {message}: {type(e).__name__}: {e}")

    def _record(self, f: TradeFill) -> None:
        t = dt.datetime.fromtimestamp(f.ts_event / 1e9, dt.UTC)
        day = t.date().isoformat()
        rec = self.ledger.apply_fill(
            day=day,
            ticker=f.instrument_id,
            side=f.side,
            quantity=Decimal(f.quantity),
            price=Decimal(f.price),
            commission=Decimal(f.commission),
            multiplier=Decimal(f.multiplier),
            commission_in_quote_ccy=f.commission_currency in ("", f.currency),
        )
        folder = self.directory / day
        self.writer.append(
            folder / ticker_file_name(f.instrument_id),
            FILL_FIELDS,
            (
                t.isoformat(timespec="milliseconds"), t.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S"),
                f.instrument_id, f.side, f.quantity, f.price, f.commission, f.commission_currency,
                str(rec.position_after), f"{rec.avg_cost_after:.6f}", f"{rec.realized_gross:.2f}",
                f"{rec.realized_net:.2f}", f"{rec.day_realized_net:.2f}",
                ";".join(f"{sid}:{q}" for sid, q in f.allocation), f.trade_id, f.venue_order_id, f.client_order_id,
            ),
        )
        self.writer.rewrite(folder / "summary.csv", SUMMARY_FIELDS, self.ledger.summary_rows(day))
        self.log.info(
            f"JOURNAL {f.instrument_id} {f.side} {f.quantity}@{f.price} realised {rec.realized_net:.2f} "
            f"(day {rec.day_realized_net:.2f})",
        )
        self._save()

    def _save(self) -> None:
        today = self.clock.utc_now().date()
        keep = {(today - dt.timedelta(days=i)).isoformat() for i in range(2)}
        self.store.save(self.ledger.to_dict(keep))
