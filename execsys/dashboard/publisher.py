"""
StatusPublisher: read-only snapshot of the running node for the terminal dashboard.

Every `dashboard.interval_ms` it collects prices, strategy state, positions, orders, risk state and
connection info from the Cache/Portfolio and the app topics, and hands a plain dict to a
JsonStateStore, which writes `dashboard.status_file` on its own thread. It never sends orders.
It is a Nautilus Strategy only because DataActor has no portfolio access.
"""

from __future__ import annotations

import datetime as dt
from collections import deque
from dataclasses import dataclass
from typing import Any

from nautilus_trader.model import BarType
from nautilus_trader.model import Currency
from nautilus_trader.model import InstrumentId
from nautilus_trader.model import StrategyId
from nautilus_trader.trading import Strategy
from nautilus_trader.trading import StrategyConfig

from execsys.config import Settings
from execsys.contracts import TOPIC_RISK_STATE
from execsys.contracts import TOPIC_SIGNAL_ALL
from execsys.contracts import RiskStateChanged
from execsys.contracts import Signal
from execsys.persistence.state_store import JsonStateStore


_TIMER = "status_publisher.snapshot"
PAPER_PORTS = (4002, 7497)
MAX_RECENT = 10


def enum_name(value: Any) -> str:
    return str(value).split(".")[-1]


def mask_account(account: str | None) -> str:
    if not account:
        return "-"
    return account[:2] + "*" * max(0, len(account) - 5) + account[-3:] if len(account) > 5 else account


@dataclass(frozen=True)
class StatusPublisherConfig:
    settings: Settings
    data_only: bool = False


class StatusPublisher(Strategy):
    # Nautilus constructors take a single argument, hence the config object
    def __init__(self, config: StatusPublisherConfig) -> None:
        super().__init__(StrategyConfig(strategy_id=StrategyId.from_str("STATUS-PUBLISHER"), order_id_tag="SP"))
        self.settings = s = config.settings
        self.data_only = config.data_only
        self.store = JsonStateStore(s.dashboard.status_file)
        self.instrument_ids = [InstrumentId.from_str(i) for i in s.universe]
        self._base_ccy = Currency.from_str(s.risk_limits.base_currency)
        self._started_ns = 0
        self._risk = {"state": "ACTIVE", "reasons": []}
        self._connection: dict[str, str] = {}
        self._signals: deque[dict[str, Any]] = deque(maxlen=MAX_RECENT)
        # strategy_id -> instrument -> {"dir", "ts_ns", "reason"}
        self._directions: dict[str, dict[str, dict[str, Any]]] = {}
        self._signal_counts: dict[str, int] = {}
        self._bar_types = {
            st.strategy_id: {i: BarType.from_str(f"{i}-{st.params['bar_spec']}") for i in st.instruments}
            for st in s.enabled_strategies
            if st.params.get("bar_spec")
        }

    def on_start(self) -> None:
        self._started_ns = self.clock.timestamp_ns()
        self.store.start()
        self.subscribe_topic(TOPIC_SIGNAL_ALL, self._on_signal)
        self.subscribe_topic(TOPIC_RISK_STATE, self._on_risk)
        self.subscribe_socket_state()
        self.clock.set_timer(
            _TIMER,
            dt.timedelta(milliseconds=self.settings.dashboard.interval_ms),
            callback=self._publish,
            fire_immediately=True,
        )

    def on_stop(self) -> None:
        self.clock.cancel_timer(_TIMER)
        snapshot = self.snapshot()
        snapshot["stopped"] = True
        self.store.save(snapshot)
        self.store.stop()

    # -- inputs ----------------------------------------------------------------------------

    def _on_signal(self, message: object) -> None:
        if not isinstance(message, Signal):
            return
        self._signal_counts[message.strategy_id] = self._signal_counts.get(message.strategy_id, 0) + 1
        self._directions.setdefault(message.strategy_id, {})[message.instrument_id] = {
            "dir": message.direction,
            "ts_ns": message.ts_init,
            "reason": message.reason,
        }
        self._signals.appendleft(
            {
                "ts_ns": message.ts_init,
                "strategy": message.strategy_id,
                "ticker": message.instrument_id,
                "dir": message.direction,
                "reason": message.reason,
            },
        )

    def _on_risk(self, message: object) -> None:
        if isinstance(message, RiskStateChanged):
            self._risk = {"state": str(message.state), "reasons": list(message.reasons)}

    def on_socket_state(self, event: Any) -> None:
        self._connection[str(event.client_id)] = enum_name(event.state)

    # -- snapshot --------------------------------------------------------------------------

    def _publish(self, _event: Any) -> None:
        try:
            self.store.save(self.snapshot())
        except Exception as e:  # the dashboard must never disturb trading
            self.log.error(f"status snapshot failed: {type(e).__name__}: {e}")

    def snapshot(self) -> dict[str, Any]:
        s, ib = self.settings, self.settings.ib
        users: dict[str, list[str]] = {}
        for st in s.enabled_strategies:
            for i in st.instruments:
                users.setdefault(i, []).append(st.strategy_id)
        return {
            "version": 1,
            "ts_ns": self.clock.timestamp_ns(),
            "started_ns": self._started_ns,
            "node": {
                "name": s.node.name,
                "trader_id": s.node.trader_id,
                "client_id": ib.client_id,
                "host": ib.host,
                "port": ib.port,
                "mode": "PAPER" if ib.port in PAPER_PORTS else "LIVE",
                "account": mask_account(ib.account_id),
                "data_only": self.data_only,
                "market_data": ib.market_data_type,
            },
            "connection": dict(self._connection) or {"IB": "CONNECTED"},
            "risk": dict(self._risk),
            "tickers": [self._ticker(iid, users.get(str(iid), [])) for iid in self.instrument_ids],
            "strategies": [self._strategy(st) for st in s.enabled_strategies],
            "signals": list(self._signals),
            "orders": self._orders(),
        }

    def _ticker(self, iid: InstrumentId, strategies: list[str]) -> dict[str, Any]:
        trade, quote = self.cache.trade(iid), self.cache.quote(iid)
        bid = ask = ltp = None
        src = ""
        ts = 0
        if quote is not None:
            bid, ask = quote.bid_price.as_decimal(), quote.ask_price.as_decimal()
            ltp, src, ts = (bid + ask) / 2, "mid", quote.ts_init
        if trade is not None and trade.ts_init >= ts - 60_000_000_000:
            ltp, src, ts = trade.price.as_decimal(), "last", max(ts, trade.ts_init)
        if ltp is None:  # no quotes or trades yet: fall back to the newest strategy bar close
            bars = [self.cache.bar(bt) for per in self._bar_types.values() for i, bt in per.items() if i == str(iid)]
            newest = max((b for b in bars if b is not None), key=lambda b: b.ts_init, default=None)
            if newest is not None:
                ltp, src, ts = newest.close.as_decimal(), "bar", newest.ts_init
        upnl = self.portfolio.unrealized_pnl(iid, target_currency=self._base_ccy)
        return {
            "id": str(iid),
            "ltp": None if ltp is None else str(ltp),
            "ltp_src": src,
            "bid": None if bid is None else str(bid),
            "ask": None if ask is None else str(ask),
            "ts_ns": ts,
            "position": str(self.portfolio.net_position(iid)),
            "upnl": None if upnl is None else str(upnl.as_decimal()),
            "strategies": strategies,
        }

    def _strategy(self, st: Any) -> dict[str, Any]:
        directions = self._directions.get(st.strategy_id, {})
        tickers = []
        for i in st.instruments:
            bar_type = self._bar_types.get(st.strategy_id, {}).get(i)
            bar = self.cache.bar(bar_type) if bar_type is not None else None
            d = directions.get(i, {})
            tickers.append(
                {
                    "id": i,
                    "dir": d.get("dir"),
                    "signal_ns": d.get("ts_ns", 0),
                    "reason": d.get("reason", ""),
                    "bar_close": None if bar is None else str(bar.close),
                    "bar_ns": 0 if bar is None else bar.ts_event,
                },
            )
        params = {k: v for k, v in st.params.items() if k not in ("bar_spec", "warmup_days")}
        return {
            "id": st.strategy_id,
            "cls": st.class_path.rsplit(":", 1)[-1],
            "timeframe": str(st.params.get("bar_spec", "-")),
            "size": str(st.size),
            "params": ", ".join(f"{k}={v}" for k, v in params.items()),
            "signals": self._signal_counts.get(st.strategy_id, 0),
            "tickers": tickers,
        }

    def _orders(self) -> list[dict[str, Any]]:
        orders = sorted(self.cache.orders(), key=lambda o: o.ts_last, reverse=True)[:MAX_RECENT]
        return [
            {
                "ts_ns": o.ts_last,
                "strategy": str(o.strategy_id),
                "ticker": str(o.instrument_id),
                "side": enum_name(o.side),
                "type": type(o).__name__.removesuffix("Order").upper() or "-",
                "qty": str(o.quantity),
                "filled": str(o.filled_qty),
                "avg_px": "" if o.avg_px is None else str(o.avg_px),
                "status": enum_name(o.status),
            }
            for o in orders
        ]
