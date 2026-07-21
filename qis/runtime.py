"""Composition root for the event-driven QIS platform."""

from __future__ import annotations

from dataclasses import dataclass

from qis.app.market_data import MarketDataApp, MarketDataEngine
from qis.config import Settings
from qis.event import EventEngine
from qis.gateway import OkxGateway
from qis.okx import OkxClient
from qis.trader.engine import MainEngine


@dataclass(slots=True)
class QisRuntime:
    """Owned platform components for one CLI process or service."""

    main_engine: MainEngine
    okx: OkxGateway
    market_data: MarketDataEngine

    @property
    def event_engine(self) -> EventEngine:
        return self.main_engine.event_engine

    def close(self) -> None:
        self.main_engine.close()

    def __enter__(self) -> "QisRuntime":
        self.main_engine.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def create_runtime(
    settings: Settings,
    *,
    okx_client: OkxClient | None = None,
    auto_start: bool = True,
) -> QisRuntime:
    """Create the standard headless platform with OKX and market-data app."""

    event_engine = EventEngine()
    main_engine = MainEngine(event_engine, auto_start=auto_start)
    okx = main_engine.add_gateway(OkxGateway, client=okx_client)
    main_engine.connect(
        {
            "api_key": settings.okx_api_key,
            "api_secret": settings.okx_api_secret,
            "passphrase": settings.okx_api_passphrase,
            "simulated": settings.okx_simulated,
        },
        okx.gateway_name,
    )
    market_data = main_engine.add_app(MarketDataApp)
    if not isinstance(market_data, MarketDataEngine):
        main_engine.close()
        raise TypeError("MarketDataApp installed an invalid engine")
    return QisRuntime(main_engine, okx, market_data)
