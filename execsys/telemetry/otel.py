"""
OpenTelemetry setup: traces, metrics and logs exported over OTLP to an OTel Collector.

Hot-path rule: handlers only record a value or start/end a span. Export happens on the SDK's
background threads (BatchSpanProcessor, PeriodicExportingMetricReader, BatchLogRecordProcessor).
Before `setup_telemetry` runs (e.g. in unit tests) every call is a cheap no-op.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import threading
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path

from opentelemetry import metrics
from opentelemetry import trace
from opentelemetry.metrics import CallbackOptions
from opentelemetry.metrics import Observation
from opentelemetry.trace import Span
from opentelemetry.trace import Status
from opentelemetry.trace import StatusCode

from execsys.config import TelemetrySettings


_SCOPE = "execsys"
_log = logging.getLogger("execsys.telemetry")


def setup_telemetry(settings: TelemetrySettings, log_dir: str, attributes: dict[str, str]) -> Callable[[], None]:
    """
    Configure global OTel providers and Python logging. Returns a shutdown function that
    flushes everything; call it after the node stops.
    """
    _setup_python_logging(log_dir)
    if not settings.enabled:
        return lambda: None

    from opentelemetry._logs import set_logger_provider
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs import LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({"service.name": settings.service_name, **attributes})

    if settings.console:
        from opentelemetry.sdk._logs.export import ConsoleLogExporter
        from opentelemetry.sdk.metrics.export import ConsoleMetricExporter
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter

        span_exporter, metric_exporter, log_exporter = (
            ConsoleSpanExporter(),
            ConsoleMetricExporter(),
            ConsoleLogExporter(),
        )
    else:
        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        endpoint = settings.otlp_endpoint
        insecure = endpoint.startswith("http://")
        span_exporter = OTLPSpanExporter(endpoint=endpoint, insecure=insecure)
        metric_exporter = OTLPMetricExporter(endpoint=endpoint, insecure=insecure)
        log_exporter = OTLPLogExporter(endpoint=endpoint, insecure=insecure)

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
    trace.set_tracer_provider(tracer_provider)

    reader = PeriodicExportingMetricReader(metric_exporter, export_interval_millis=settings.export_interval_ms)
    meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(meter_provider)

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
    set_logger_provider(logger_provider)
    logging.getLogger("execsys").addHandler(LoggingHandler(level=logging.INFO, logger_provider=logger_provider))

    # Rebuild instruments against the real providers
    global _metrics, _tracer
    _metrics = Metrics()
    _tracer = OrderTracer()

    def shutdown() -> None:
        for provider in (tracer_provider, meter_provider, logger_provider):
            try:
                provider.shutdown()
            except Exception:  # never let telemetry break shutdown
                _log.exception("telemetry shutdown failed")

    return shutdown


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        span = trace.get_current_span().get_span_context()
        if span.is_valid:
            payload["trace_id"] = f"{span.trace_id:032x}"
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def _setup_python_logging(log_dir: str) -> None:
    """
    Python-side logs (background threads, alerts, ops) as JSON lines. Nautilus components log
    through their own logger to `logs/` as configured in app.py.
    """
    root = logging.getLogger("execsys")
    if any(getattr(h, "_execsys", False) for h in root.handlers):
        return
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.TimedRotatingFileHandler(
        Path(log_dir) / "execsys.jsonl",
        when="midnight",
        backupCount=30,
        encoding="utf-8",
    )
    handler.setFormatter(_JsonFormatter())
    handler._execsys = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(logging.INFO)


class Metrics:
    """
    All metric instruments in one place so names and attributes stay consistent.
    Attribute keys: strategy_id, instrument_id, reason, state.
    """

    def __init__(self) -> None:
        m = metrics.get_meter(_SCOPE)
        self.signals = m.create_counter("execsys.signals", description="Signals published")
        self.orders_submitted = m.create_counter("execsys.orders.submitted")
        self.orders_denied = m.create_counter("execsys.orders.denied")
        self.orders_rejected = m.create_counter("execsys.orders.rejected")
        self.fills = m.create_counter("execsys.fills")
        self.signal_to_submit_ms = m.create_histogram("execsys.latency.signal_to_submit", unit="ms")
        self.submit_to_fill_ms = m.create_histogram("execsys.latency.submit_to_fill", unit="ms")
        self.data_to_signal_ms = m.create_histogram("execsys.latency.data_to_signal", unit="ms")
        self.handler_errors = m.create_counter("execsys.handler.errors")
        self.recorder_dropped = m.create_counter("execsys.recorder.dropped")
        self.recorder_written = m.create_counter("execsys.recorder.written")
        self.alerts = m.create_counter("execsys.alerts")
        self._gauges: dict[str, Callable[[], list[tuple[float, dict[str, str]]]]] = {}
        self._lock = threading.Lock()
        m.create_observable_gauge("execsys.gauge", callbacks=[self._observe])

    def register_gauge(self, name: str, read: Callable[[], list[tuple[float, dict[str, str]]]]) -> None:
        """
        Register a gauge read on the exporter thread. `read` must be thread-safe and cheap,
        returning (value, attributes) pairs. Exported as `execsys.gauge{name=...}`.
        """
        with self._lock:
            self._gauges[name] = read

    def _observe(self, _options: CallbackOptions) -> list[Observation]:
        with self._lock:
            gauges = list(self._gauges.items())
        out: list[Observation] = []
        for name, read in gauges:
            try:
                out.extend(Observation(v, {"name": name, **attrs}) for v, attrs in read())
            except Exception:
                _log.exception("gauge %s failed", name)
        return out


class OrderTracer:
    """
    One span per order: opened at submit, closed on fill/cancel/reject/deny.
    Spans are kept in a bounded map so a lost terminal event cannot leak memory.
    """

    def __init__(self, max_open: int = 10_000) -> None:
        self._tracer = trace.get_tracer(_SCOPE)
        self._open: OrderedDict[str, Span] = OrderedDict()
        self._max_open = max_open

    def start(self, client_order_id: str, attributes: dict[str, str | float | int]) -> None:
        span = self._tracer.start_span("order", attributes=attributes)
        self._open[client_order_id] = span
        if len(self._open) > self._max_open:
            _, old = self._open.popitem(last=False)
            old.set_status(Status(StatusCode.ERROR, "evicted"))
            old.end()

    def event(self, client_order_id: str, name: str, attributes: dict[str, str | float | int] | None = None) -> None:
        span = self._open.get(client_order_id)
        if span is not None:
            span.add_event(name, attributes=attributes or {})

    def end(self, client_order_id: str, outcome: str, error: bool = False) -> None:
        span = self._open.pop(client_order_id, None)
        if span is None:
            return
        span.set_attribute("outcome", outcome)
        if error:
            span.set_status(Status(StatusCode.ERROR, outcome))
        span.end()


_metrics: Metrics | None = None
_tracer: OrderTracer | None = None


def get_metrics() -> Metrics:
    global _metrics
    if _metrics is None:
        _metrics = Metrics()
    return _metrics


def get_order_tracer() -> OrderTracer:
    global _tracer
    if _tracer is None:
        _tracer = OrderTracer()
    return _tracer
