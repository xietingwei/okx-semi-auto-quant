"""Market-data application engine with a small in-memory bar cache."""

from __future__ import annotations

from threading import RLock

from qis.event import Event, EventEngine
from qis.models import Candle
from qis.trader.app import BaseApp
from qis.trader.engine import BaseEngine, MainEngine
from qis.trader.event import EVENT_BAR
from qis.trader.gateway import BaseGateway
from qis.trader.object import BarBatchData, HistoryRequest


class MarketDataEngine(BaseEngine):
    engine_name = "market_data"

    def __init__(self, main_engine: MainEngine, event_engine: EventEngine) -> None:
        super().__init__(main_engine, event_engine)
        self._history: dict[tuple[str, str, str], tuple[Candle, ...]] = {}
        self._lock = RLock()
        self.event_engine.register(EVENT_BAR, self._process_bar_event)

    def gateway(self, gateway_name: str = "OKX") -> BaseGateway:
        return self.main_engine.get_gateway(gateway_name)

    def query_history(
        self,
        request: HistoryRequest,
        gateway_name: str = "OKX",
    ) -> list[Candle]:
        candles = self.gateway(gateway_name).query_history(request)
        with self._lock:
            self._history[(gateway_name, request.inst_id, request.bar)] = tuple(candles)
        return candles

    def cached_history(
        self,
        inst_id: str,
        bar: str,
        gateway_name: str = "OKX",
    ) -> tuple[Candle, ...]:
        with self._lock:
            return self._history.get((gateway_name, inst_id, bar), ())

    def close(self) -> None:
        self.event_engine.unregister(EVENT_BAR, self._process_bar_event)
        with self._lock:
            self._history.clear()

    def _process_bar_event(self, event: Event) -> None:
        data = event.data
        if not isinstance(data, BarBatchData):
            return
        with self._lock:
            self._history[(data.gateway_name, data.inst_id, data.bar)] = data.candles


class MarketDataApp(BaseApp):
    app_name = "market_data"
    display_name = "行情数据"
    engine_class = MarketDataEngine
