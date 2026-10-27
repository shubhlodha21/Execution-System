"""
Monitoring actor: collects alert-worthy events from the bus (app alerts, IB socket state, queue
pressure) and hands them to the AlertDispatcher. It never blocks: dispatch is a queue put.
"""

from __future__ import annotations

from typing import Any

from nautilus_trader.common import DataActor
from nautilus_trader.config import DataActorConfig
from nautilus_trader.model import ActorId

from execsys.config import Settings
from execsys.contracts import TOPIC_ALERT
from execsys.contracts import Alert
from execsys.contracts import Severity
from execsys.alerting.dispatcher import AlertDispatcher
from execsys.telemetry import get_metrics


class MonitoringActor(DataActor):
    def __init__(self, settings: Settings, dispatcher: AlertDispatcher | None = None) -> None:
        super().__init__(DataActorConfig(actor_id=ActorId.from_str("MONITORING-001")))
        self.enabled = settings.alerts.enabled
        self.dispatcher = dispatcher or AlertDispatcher(settings.alerts)
        self._metrics = get_metrics()

    def on_start(self) -> None:
        if self.enabled:
            self.dispatcher.start()
        self.subscribe_topic(TOPIC_ALERT, self._on_alert)
        self.subscribe_socket_state()
        self.subscribe_queue_state()
        self._send(Alert(Severity.INFO, "node", "Execution system started", self.clock.timestamp_ns()))

    def on_stop(self) -> None:
        self._send(Alert(Severity.WARNING, "node", "Execution system stopping", self.clock.timestamp_ns()))
        self.dispatcher.stop()

    def _on_alert(self, message: object) -> None:
        if isinstance(message, Alert):
            self._send(message)

    def on_socket_state(self, event: Any) -> None:
        state = str(event.state).split(".")[-1]
        healthy = state.upper() in ("ACTIVE", "CONNECTED", "OPEN")
        severity = Severity.INFO if healthy else Severity.CRITICAL
        self._send(
            Alert(severity, "connectivity", f"{event.client_id} {event.endpoint} socket {state}", event.ts_event),
        )

    def on_queue_state(self, event: Any) -> None:
        self._send(
            Alert(
                Severity.WARNING,
                "event_queue",
                f"{event.channel} queue {event.condition} ({event.state})",
                self.clock.timestamp_ns(),
            ),
        )

    def _send(self, alert: Alert) -> None:
        self._metrics.alerts.add(1, {"severity": str(alert.severity), "module": alert.module})
        if self.enabled:
            self.dispatcher.submit(alert)
