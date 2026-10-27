"""
Bounded queue + background batch writer + pluggable sinks.

The event thread only calls `BatchWriter.put` (non-blocking). A daemon thread drains the queue
every `batch_size` rows or `flush_interval` seconds and hands batches to the sink. A full
queue drops the row and counts it, so recording can never slow down trading.
"""

from __future__ import annotations

import csv
import logging
import queue
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import IO
from typing import Protocol


_log = logging.getLogger("execsys.recorder")

# Compact tick schema (agreed in Diagrams/Modules.xlsx) and bar schema
TICK_FIELDS = (
    "schema",
    "tick_type",
    "asset_class",
    "instrument_id",
    "ts_event_utc",
    "ts_init_ns",
    "mid_price",
    "last_price",
    "last_size",
    "aggressor_side",
)
BAR_FIELDS = (
    "schema",
    "bar_type",
    "asset_class",
    "instrument_id",
    "ts_event_utc",
    "ts_init_ns",
    "open",
    "high",
    "low",
    "close",
    "volume",
)
SCHEMAS = {"tick": TICK_FIELDS, "bar": BAR_FIELDS}

Row = tuple[str, ...]  # first element is the schema name


class Sink(Protocol):
    def write(self, schema: str, rows: list[Row]) -> None: ...
    def close(self) -> None: ...


class CsvSink:
    """
    One CSV per schema per UTC day: <dir>/<YYYY-MM-DD>/<schema>.csv
    """

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self._files: dict[tuple[str, str], tuple[IO[str], csv.writer]] = {}

    def write(self, schema: str, rows: list[Row]) -> None:
        day = time.strftime("%Y-%m-%d", time.gmtime())
        key = (day, schema)
        if key not in self._files:
            self._rotate(day)
            path = self.directory / day / f"{schema}.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            new = not path.exists()
            f = path.open("a", newline="", encoding="utf-8")
            w = csv.writer(f)
            if new:
                w.writerow(SCHEMAS[schema])
            self._files[key] = (f, w)
        f, w = self._files[key]
        w.writerows(rows)
        f.flush()

    def _rotate(self, day: str) -> None:
        for (d, s), (f, _) in list(self._files.items()):
            if d != day:
                f.close()
                del self._files[(d, s)]

    def close(self) -> None:
        for f, _ in self._files.values():
            f.close()
        self._files.clear()


class BatchWriter:
    def __init__(self, sink: Sink, queue_size: int, batch_size: int, flush_interval: float) -> None:
        self.sink = sink
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self._q: queue.Queue[Row | None] = queue.Queue(maxsize=queue_size)
        self._thread: threading.Thread | None = None
        self.dropped = 0
        self.written = 0
        self.on_dropped = lambda n: None
        self.on_written = lambda n: None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="recorder-writer", daemon=True)
            self._thread.start()

    def put(self, row: Row) -> None:
        try:
            self._q.put_nowait(row)
        except queue.Full:
            self.dropped += 1
            self.on_dropped(1)

    def depth(self) -> int:
        return self._q.qsize()

    def stop(self, timeout: float = 10.0) -> None:
        """
        Flush everything queued, then stop the thread.
        """
        if self._thread is None:
            return
        self._q.put(None)  # blocking put: the sentinel must get in
        self._thread.join(timeout=timeout)
        self._thread = None
        self.sink.close()

    def _run(self) -> None:
        batch: list[Row] = []
        deadline = time.monotonic() + self.flush_interval
        while True:
            timeout = max(0.0, deadline - time.monotonic())
            try:
                row = self._q.get(timeout=timeout)
            except queue.Empty:
                row = ...
            if row is None:
                self._flush(batch)
                return
            if row is not ...:
                batch.append(row)
            if len(batch) >= self.batch_size or time.monotonic() >= deadline:
                self._flush(batch)
                batch = []
                deadline = time.monotonic() + self.flush_interval

    def _flush(self, batch: list[Row]) -> None:
        if not batch:
            return
        by_schema: dict[str, list[Row]] = defaultdict(list)
        for row in batch:
            by_schema[row[0]].append(row)
        for schema, rows in by_schema.items():
            for attempt in range(3):
                try:
                    self.sink.write(schema, rows)
                    self.written += len(rows)
                    self.on_written(len(rows))
                    break
                except Exception:
                    _log.exception("sink write failed (attempt %d) for %d %s rows", attempt + 1, len(rows), schema)
                    time.sleep(0.5 * (attempt + 1))
            else:
                self.dropped += len(rows)
                self.on_dropped(len(rows))
