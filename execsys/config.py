"""
Typed configuration for the whole system.

One YAML file (config/settings.yaml) describes the node, universe, strategies and limits.
Secrets (IB account, alert tokens) come only from environment variables.
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from typing import Any
from typing import Literal

import yaml
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class IBSettings(_Model):
    host: str = "127.0.0.1"
    port: int = 4002  # 4002 Gateway paper, 4001 Gateway live
    client_id: int = 99
    market_data_type: Literal["realtime", "frozen", "delayed", "delayed-frozen"] = "realtime"
    connection_timeout_secs: int = 10
    request_timeout_secs: int = 30
    account_id: str | None = None  # from env IB_ACCOUNT_ID; None = data-only node

    @field_validator("client_id")
    @classmethod
    def _client_id(cls, v: int) -> int:
        if v % 1000 == 0:
            raise ValueError("client_id must not be a multiple of 1000 (IB adapter order-id partitioning)")
        return v


class NodeSettings(_Model):
    name: str = "EXECSYS"
    trader_id: str = "TRADER-001"
    reconciliation: bool = True
    reconciliation_lookback_mins: int = 1440
    log_dir: str = "logs"
    log_level: str = "INFO"


class RiskEngineSettings(_Model):
    """
    Pre-trade checks enforced by the Nautilus Risk Engine on every order.
    """

    max_order_submit_rate: str = "40/00:00:01"  # IB allows ~50 msgs/sec per connection
    max_order_modify_rate: str = "40/00:00:01"
    max_notional_per_order: dict[str, str] = Field(default_factory=dict)  # instrument_id -> notional


class RiskLimits(_Model):
    """
    Account-level limits enforced by the Risk Monitor.
    """

    base_currency: str = "USD"  # PnL and exposure of every instrument are converted to this before summing
    max_daily_loss: Decimal = Decimal("1000")  # positive number, in base_currency
    max_position_per_instrument: dict[str, Decimal] = Field(default_factory=dict)
    default_max_position: Decimal = Decimal("100")
    max_gross_exposure: Decimal = Decimal("250000")
    stale_data_secs: int = 120  # no quote/trade for this long on any traded instrument -> REDUCING
    max_rejects_per_window: int = 5
    reject_window_secs: int = 60
    flatten_on_halt: bool = False
    check_interval_ms: int = 1000


class OrderManagerSettings(_Model):
    strategy_id: str = "ORDER-MANAGER"  # Nautilus appends "-<order_id_tag>"
    order_id_tag: str = "001"
    order_type: Literal["MARKET", "LIMIT"] = "MARKET"
    limit_offset_ticks: int = 1  # LIMIT: price = touch +/- ticks (marketable limit)
    signal_stale_secs: int = 300  # ignore signals older than this
    min_order_interval_ms: int = 500  # per instrument throttle
    state_file: str = "runtime/order_manager_state.json"
    flatten_on_stop: bool = False


class StrategySettings(_Model):
    strategy_id: str
    class_path: str  # "module.path:ClassName"
    enabled: bool = True
    instruments: list[str]
    size: Decimal  # position size per instrument when direction = +/-1
    params: dict[str, Any] = Field(default_factory=dict)


class RecorderSettings(_Model):
    enabled: bool = True
    directory: str = "data/recorded"
    record_quotes: bool = True
    record_trades: bool = True
    record_bars: bool = True
    bar_spec: str = "1-MINUTE-LAST-EXTERNAL"
    queue_size: int = 200_000
    batch_size: int = 500
    flush_interval_secs: float = 1.0


class TelemetrySettings(_Model):
    enabled: bool = True
    service_name: str = "execsys"
    otlp_endpoint: str = "http://127.0.0.1:4317"  # OTel Collector gRPC (IPv4: "localhost" may resolve to ::1)
    export_interval_ms: int = 5000
    console: bool = False  # print spans/metrics to stdout instead (debugging)


class AlertSettings(_Model):
    enabled: bool = True
    dedupe_secs: int = 300
    max_per_minute: int = 20
    telegram_bot_token: str | None = None  # from env TELEGRAM_BOT_TOKEN
    telegram_chat_id: str | None = None  # from env TELEGRAM_CHAT_ID


class DashboardSettings(_Model):
    enabled: bool = True  # StatusPublisher writes a snapshot for `python -m execsys.dashboard`
    status_file: str = "runtime/status.json"
    interval_ms: int = 1000


class OpsSettings(_Model):
    control_file: str = "runtime/control.json"
    risk_state_file: str = "runtime/risk_state.json"


class Settings(_Model):
    node: NodeSettings = NodeSettings()
    ib: IBSettings = IBSettings()
    risk_engine: RiskEngineSettings = RiskEngineSettings()
    risk_limits: RiskLimits = RiskLimits()
    order_manager: OrderManagerSettings = OrderManagerSettings()
    strategies: list[StrategySettings] = Field(default_factory=list)
    recorder: RecorderSettings = RecorderSettings()
    telemetry: TelemetrySettings = TelemetrySettings()
    alerts: AlertSettings = AlertSettings()
    ops: OpsSettings = OpsSettings()
    dashboard: DashboardSettings = DashboardSettings()

    @model_validator(mode="after")
    def _unique_ids(self) -> Settings:
        ids = [s.strategy_id for s in self.strategies] + [self.order_manager.strategy_id]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate strategy ids: {sorted(dupes)}")
        return self

    @property
    def enabled_strategies(self) -> list[StrategySettings]:
        return [s for s in self.strategies if s.enabled]

    @property
    def universe(self) -> list[str]:
        """
        Every instrument any enabled strategy trades, in first-seen order.
        """
        seen: dict[str, None] = {}
        for s in self.enabled_strategies:
            for i in s.instruments:
                seen.setdefault(i, None)
        return list(seen)

    def max_position(self, instrument_id: str) -> Decimal:
        return self.risk_limits.max_position_per_instrument.get(
            instrument_id,
            self.risk_limits.default_max_position,
        )


# Secrets and per-machine overrides read from the environment
_ENV_OVERRIDES: dict[str, tuple[str, str]] = {
    "IB_ACCOUNT_ID": ("ib", "account_id"),
    "IB_HOST": ("ib", "host"),
    "IB_PORT": ("ib", "port"),
    "IB_CLIENT_ID": ("ib", "client_id"),
    "TELEGRAM_BOT_TOKEN": ("alerts", "telegram_bot_token"),
    "TELEGRAM_CHAT_ID": ("alerts", "telegram_chat_id"),
    "OTEL_EXPORTER_OTLP_ENDPOINT": ("telemetry", "otlp_endpoint"),
}


def load_settings(path: str | Path = "config/settings.yaml", env: dict[str, str] | None = None) -> Settings:
    env = dict(os.environ) if env is None else env
    raw: dict[str, Any] = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    for var, (section, key) in _ENV_OVERRIDES.items():
        if env.get(var):
            raw.setdefault(section, {})[key] = env[var]
    return Settings.model_validate(raw)
