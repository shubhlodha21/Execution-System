"""
Node bootstrap: settings -> telemetry -> LiveNode (one IB client_id) -> components -> run.

No trading logic lives here. Adding a strategy is a config change (config/settings.yaml).

    python -m execsys.app [--config config/settings.yaml] [--data-only]
"""

from __future__ import annotations

import argparse
import importlib
import logging

from nautilus_trader.adapters import interactive_brokers as ib
from nautilus_trader.common import Environment
from nautilus_trader.common import FileWriterConfig
from nautilus_trader.common import LoggerConfig
from nautilus_trader.common import LogLevel
from nautilus_trader.config import LiveRiskEngineConfig
from nautilus_trader.live import LiveNode
from nautilus_trader.model import InstrumentId
from nautilus_trader.model import TraderId

from execsys.config import Settings
from execsys.config import StrategySettings
from execsys.config import load_settings
from execsys.telemetry import setup_telemetry


_log = logging.getLogger("execsys.app")

_MARKET_DATA_TYPES = {
    "realtime": ib.MarketDataType.REALTIME,
    "frozen": ib.MarketDataType.FROZEN,
    "delayed": ib.MarketDataType.DELAYED,
    "delayed-frozen": ib.MarketDataType.DELAYED_FROZEN,
}


def _load_class(path: str) -> type:
    module, _, name = path.partition(":")
    return getattr(importlib.import_module(module), name)


def build_strategy(s: StrategySettings) -> object:
    return _load_class(s.class_path)(s)


def build_node(settings: Settings, data_only: bool = False, instruments: list[str] | None = None) -> LiveNode:
    """
    `instruments` overrides which contracts are loaded (tools such as the FX monitor);
    by default the node loads `settings.universe`.
    """
    n, cfg = settings.node, settings.ib
    provider = ib.InteractiveBrokersInstrumentProviderConfig(
        symbology_method=ib.SymbologyMethod.SIMPLIFIED,
        load_ids={InstrumentId.from_str(i) for i in (instruments or settings.universe)},
        load_contracts=[],
        build_options_chain=False,
        build_futures_chain=False,
        symbol_to_mic_venue={},
    )
    level = getattr(LogLevel, n.log_level.upper())

    builder = LiveNode.builder(n.name, TraderId.from_str(n.trader_id), Environment.LIVE)
    builder = builder.with_logging(
        LoggerConfig(
            stdout_level=level,
            fileout_level=level,
            file_config=FileWriterConfig(directory=n.log_dir, file_name="nautilus", file_format="json"),
        ),
    )
    builder = builder.with_risk_engine_config(
        LiveRiskEngineConfig(
            bypass=False,
            max_order_submit_rate=settings.risk_engine.max_order_submit_rate,
            max_order_modify_rate=settings.risk_engine.max_order_modify_rate,
            max_notional_per_order=dict(settings.risk_engine.max_notional_per_order),
        ),
    )
    builder = builder.with_timeout_connection(cfg.connection_timeout_secs + 5)
    builder = builder.with_timeout_reconciliation(10)
    builder = builder.with_timeout_portfolio(10)
    builder = builder.with_timeout_disconnection_secs(5)
    builder = builder.with_delay_post_stop_secs(5)
    builder = builder.with_reconciliation(n.reconciliation and not data_only)
    builder = builder.with_reconciliation_lookback_mins(n.reconciliation_lookback_mins)
    builder = builder.add_data_client(
        None,
        ib.InteractiveBrokersDataClientFactory(),
        ib.InteractiveBrokersDataClientConfig(
            host=cfg.host,
            port=cfg.port,
            client_id=cfg.client_id,
            connection_timeout=cfg.connection_timeout_secs,
            request_timeout=cfg.request_timeout_secs,
            market_data_type=_MARKET_DATA_TYPES[cfg.market_data_type],
            instrument_provider=provider,
        ),
    )
    if not data_only:
        if not cfg.account_id:
            raise SystemExit("IB_ACCOUNT_ID is not set; set it or run with --data-only")
        builder = builder.add_exec_client(
            None,
            ib.InteractiveBrokersExecutionClientFactory(),
            ib.InteractiveBrokersExecutionClientConfig(
                host=cfg.host,
                port=cfg.port,
                client_id=cfg.client_id,  # same id: data + execution share one socket
                account_id=cfg.account_id,
                connection_timeout=cfg.connection_timeout_secs,
                request_timeout=cfg.request_timeout_secs,
                fetch_all_open_orders=False,
                instrument_provider=provider,
            ),
        )
    return builder.build()


def add_components(node: LiveNode, settings: Settings, data_only: bool = False) -> None:
    # Imported here so telemetry is configured before components create their instruments
    from execsys.alerting.monitor import MonitoringActor
    from execsys.dashboard.publisher import StatusPublisher
    from execsys.dashboard.publisher import StatusPublisherConfig
    from execsys.order_manager.manager import OrderManager
    from execsys.persistence.recorder import DataRecorder
    from execsys.risk.monitor import RiskMonitor

    node.add_actor(MonitoringActor(settings))
    if settings.recorder.enabled:
        node.add_actor(DataRecorder(settings))
    for s in settings.enabled_strategies:
        node.add_actor(build_strategy(s))
    if not data_only:
        # Risk Monitor before Order Manager so its state is known when signals start flowing
        node.add_strategy(RiskMonitor(settings))
        node.add_strategy(OrderManager(settings))
    if settings.dashboard.enabled:
        node.add_strategy(StatusPublisher(StatusPublisherConfig(settings, data_only)))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the execution system")
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--data-only", action="store_true", help="no execution client, no Order Manager")
    args = parser.parse_args()

    settings = load_settings(args.config)
    shutdown_telemetry = setup_telemetry(
        settings.telemetry,
        settings.node.log_dir,
        {"trader_id": settings.node.trader_id, "ib.client_id": str(settings.ib.client_id)},
    )
    try:
        node = build_node(settings, data_only=args.data_only)
        add_components(node, settings, data_only=args.data_only)
        _log.info("starting node %s with %d strategies", settings.node.name, len(settings.enabled_strategies))
        print(
            f"Running {settings.node.name} against {settings.ib.host}:{settings.ib.port} "
            f"(client_id {settings.ib.client_id}); press Ctrl+C to stop.",
            flush=True,
        )
        node.run()
    finally:
        shutdown_telemetry()


if __name__ == "__main__":
    main()
