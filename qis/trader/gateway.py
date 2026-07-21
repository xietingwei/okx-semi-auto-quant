"""Gateway abstraction for market and account integrations."""

from __future__ import annotations

from abc import ABC, abstractmethod

from qis.event import Event, EventEngine
from qis.models import Candle
from qis.trader.event import EVENT_BAR, EVENT_GATEWAY_STATUS, EVENT_LOG
from qis.trader.object import BarBatchData, GatewayStatusData, HistoryRequest, LogData


class BaseGateway(ABC):
    """Base class for exchange/data-source adapters.

    QIS gateways own protocol-specific clients and convert their outputs into
    stable domain events.  REST methods may still return values synchronously;
    events make the same observations available to independent applications.
    """

    default_name = ""

    def __init__(self, event_engine: EventEngine, gateway_name: str) -> None:
        self.event_engine = event_engine
        self.gateway_name = gateway_name

    def on_event(self, event_type: str, data: object = None) -> None:
        self.event_engine.put(Event(event_type, data, source=self.gateway_name))

    def write_log(self, message: str, level: str = "INFO") -> None:
        self.on_event(
            EVENT_LOG,
            LogData(message=message, source=self.gateway_name, level=level),
        )

    def on_status(self, connected: bool, detail: str) -> None:
        self.on_event(
            EVENT_GATEWAY_STATUS,
            GatewayStatusData(self.gateway_name, connected, detail),
        )

    def on_bars(self, inst_id: str, bar: str, candles: list[Candle]) -> None:
        data = BarBatchData(
            gateway_name=self.gateway_name,
            inst_id=inst_id,
            bar=bar,
            candles=tuple(candles),
        )
        self.on_event(EVENT_BAR, data)
        self.on_event(f"{EVENT_BAR}.{inst_id}.{bar}", data)

    @abstractmethod
    def connect(self, setting: dict) -> None:
        """Configure or establish the underlying data connection."""

    @abstractmethod
    def close(self) -> None:
        """Release resources owned by the gateway."""

    @abstractmethod
    def query_history(self, request: HistoryRequest) -> list[Candle]:
        """Return candle history for a normalized request."""
