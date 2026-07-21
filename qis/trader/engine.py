"""Main engine and function-engine lifecycle for QIS."""

from __future__ import annotations

from typing import TypeVar, cast

from qis.event import Event, EventEngine
from qis.trader.app import BaseApp
from qis.trader.event import EVENT_LOG
from qis.trader.gateway import BaseGateway
from qis.trader.object import LogData


EngineType = TypeVar("EngineType", bound="BaseEngine")
GatewayType = TypeVar("GatewayType", bound=BaseGateway)


class BaseEngine:
    """Base class for an independently installable QIS application engine."""

    engine_name = ""

    def __init__(self, main_engine: "MainEngine", event_engine: EventEngine) -> None:
        self.main_engine = main_engine
        self.event_engine = event_engine

    def close(self) -> None:
        return


class MainEngine:
    """Own gateways, application engines, and the shared event bus."""

    def __init__(
        self,
        event_engine: EventEngine | None = None,
        *,
        auto_start: bool = True,
    ) -> None:
        self.event_engine = event_engine or EventEngine()
        self.gateways: dict[str, BaseGateway] = {}
        self.engines: dict[str, BaseEngine] = {}
        self.apps: dict[str, BaseApp] = {}
        self._closed = False
        if auto_start:
            self.event_engine.start()

    def start(self) -> None:
        if self._closed:
            raise RuntimeError("main engine is closed")
        self.event_engine.start()

    def add_gateway(
        self,
        gateway_class: type[GatewayType],
        gateway_name: str = "",
        **kwargs,
    ) -> GatewayType:
        """Install one gateway class under a unique name."""

        self._ensure_open()
        name = gateway_name or gateway_class.default_name
        if not name:
            raise ValueError("gateway name is required")
        if name in self.gateways:
            raise ValueError(f"gateway already installed: {name}")
        gateway = gateway_class(self.event_engine, name, **kwargs)
        self.gateways[name] = gateway
        return gateway

    def add_engine(
        self,
        engine_class: type[EngineType],
        *args,
        **kwargs,
    ) -> EngineType:
        """Install one application function engine."""

        self._ensure_open()
        engine = engine_class(self, self.event_engine, *args, **kwargs)
        if not engine.engine_name:
            raise ValueError("engine_name is required")
        if engine.engine_name in self.engines:
            engine.close()
            raise ValueError(f"engine already installed: {engine.engine_name}")
        self.engines[engine.engine_name] = engine
        return engine

    def add_app(
        self,
        app_class: type[BaseApp],
        *engine_args,
        **engine_kwargs,
    ) -> BaseEngine:
        """Install app metadata and construct its function engine."""

        app = app_class()
        if not app.app_name:
            raise ValueError("app_name is required")
        if app.app_name in self.apps:
            raise ValueError(f"app already installed: {app.app_name}")
        engine = self.add_engine(app.engine_class, *engine_args, **engine_kwargs)
        self.apps[app.app_name] = app
        return engine

    def get_gateway(self, gateway_name: str) -> BaseGateway:
        try:
            return self.gateways[gateway_name]
        except KeyError as exc:
            raise KeyError(f"gateway not found: {gateway_name}") from exc

    def get_engine(self, engine_name: str) -> BaseEngine:
        try:
            return self.engines[engine_name]
        except KeyError as exc:
            raise KeyError(f"engine not found: {engine_name}") from exc

    def connect(self, setting: dict, gateway_name: str) -> None:
        gateway = self.get_gateway(gateway_name)
        self.write_log(f"connecting gateway: {gateway_name}")
        gateway.connect(setting)

    def write_log(self, message: str, source: str = "MainEngine") -> None:
        self.event_engine.put(
            Event(EVENT_LOG, LogData(message=message, source=source), source=source)
        )

    def close(self) -> None:
        """Close every component exactly once."""

        if self._closed:
            return
        self._closed = True
        for engine in reversed(tuple(self.engines.values())):
            engine.close()
        for gateway in reversed(tuple(self.gateways.values())):
            gateway.close()
        self.event_engine.stop()

    def __enter__(self) -> "MainEngine":
        self.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("main engine is closed")


def require_engine(
    main_engine: MainEngine,
    engine_name: str,
    engine_type: type[EngineType],
) -> EngineType:
    """Return a registered engine with a checked concrete type."""

    engine = main_engine.get_engine(engine_name)
    if not isinstance(engine, engine_type):
        raise TypeError(f"engine {engine_name} is not {engine_type.__name__}")
    return cast(EngineType, engine)
