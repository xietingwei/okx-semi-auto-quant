"""OKX gateway adapting the existing REST client to QIS platform events."""

from __future__ import annotations

from typing import Any

from qis.event import EventEngine
from qis.models import Candle, Side
from qis.okx import OkxClient
from qis.trader.event import EVENT_ACCOUNT, EVENT_TICK
from qis.trader.gateway import BaseGateway
from qis.trader.object import HistoryRequest, TickBatchData


class OkxGateway(BaseGateway):
    """Thread-safe, headless OKX REST gateway.

    Public methods intentionally mirror :class:`OkxClient` during migration so
    existing analysis services can consume the gateway without knowing the
    exchange protocol.  New application code should prefer ``query_history``
    and the market-data engine.
    """

    default_name = "OKX"

    def __init__(
        self,
        event_engine: EventEngine,
        gateway_name: str,
        *,
        client: OkxClient | None = None,
    ) -> None:
        super().__init__(event_engine, gateway_name)
        self._client = client
        self.connected = False

    @property
    def client(self) -> OkxClient:
        if self._client is None:
            self._client = OkxClient()
        return self._client

    def connect(self, setting: dict) -> None:
        if self._client is None:
            self._client = OkxClient(
                str(setting.get("api_key") or ""),
                str(setting.get("api_secret") or ""),
                str(setting.get("passphrase") or ""),
                bool(setting.get("simulated", True)),
            )
        self.connected = True
        mode = "simulated" if self.client.simulated else "live"
        self.on_status(True, f"OKX REST gateway configured ({mode})")

    def close(self) -> None:
        if not self.connected:
            return
        self.connected = False
        self.on_status(False, "OKX REST gateway closed")

    def query_history(self, request: HistoryRequest) -> list[Candle]:
        return self.public_range_candles(
            request.inst_id,
            request.bar,
            request.limit,
        )

    def public_candles(
        self,
        inst_id: str,
        bar: str = "15m",
        limit: int = 100,
    ) -> list[Candle]:
        candles = self.client.public_candles(inst_id, bar, limit)
        self.on_bars(inst_id, bar, candles)
        return candles

    def public_range_candles(
        self,
        inst_id: str,
        bar: str = "1D",
        limit: int = 300,
    ) -> list[Candle]:
        candles = self.client.public_range_candles(inst_id, bar, limit)
        self.on_bars(inst_id, bar, candles)
        return candles

    def public_history_candles(
        self,
        inst_id: str,
        bar: str = "1D",
        limit: int = 300,
    ) -> list[Candle]:
        candles = self.client.public_history_candles(inst_id, bar, limit)
        self.on_bars(inst_id, bar, candles)
        return candles

    def public_instrument(self, inst_id: str) -> dict[str, Any]:
        return self.client.public_instrument(inst_id)

    def public_instruments(self, inst_type: str) -> list[dict[str, Any]]:
        return self.client.public_instruments(inst_type)

    def public_tickers(self, inst_type: str) -> list[dict[str, Any]]:
        ticks = self.client.public_tickers(inst_type)
        self.on_event(
            EVENT_TICK,
            TickBatchData(self.gateway_name, inst_type, tuple(ticks)),
        )
        return ticks

    def public_order_book(self, inst_id: str, depth: int = 20) -> dict[str, Any]:
        return self.client.public_order_book(inst_id, depth)

    def public_open_interest(self, inst_type: str = "SWAP") -> list[dict[str, Any]]:
        return self.client.public_open_interest(inst_type)

    def public_funding_rate(self, inst_id: str) -> dict[str, Any]:
        return self.client.public_funding_rate(inst_id)

    def balance_equity(self, ccy: str = "USDT") -> float | None:
        equity = self.client.balance_equity(ccy)
        self.on_event(EVENT_ACCOUNT, {"currency": ccy, "equity": equity})
        return equity

    def order_size_from_base(self, inst_id: str, base_size: float) -> str:
        return self.client.order_size_from_base(inst_id, base_size)

    def place_market_order(
        self,
        inst_id: str,
        side: Side,
        size: str | float,
        td_mode: str = "cross",
    ) -> dict[str, Any]:
        return self.client.place_market_order(inst_id, side, size, td_mode)
