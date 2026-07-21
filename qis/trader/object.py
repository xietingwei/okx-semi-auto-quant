"""Typed data transfer objects crossing the QIS event boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from qis.models import Candle, utc_now


@dataclass(frozen=True, slots=True)
class LogData:
    message: str
    source: str = "MainEngine"
    level: str = "INFO"
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class GatewayStatusData:
    gateway_name: str
    connected: bool
    detail: str
    observed_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class HistoryRequest:
    inst_id: str
    bar: str = "1D"
    limit: int = 300

    def __post_init__(self) -> None:
        if not self.inst_id.strip():
            raise ValueError("inst_id is required")
        if not self.bar.strip():
            raise ValueError("bar is required")
        if self.limit <= 0:
            raise ValueError("history limit must be positive")


@dataclass(frozen=True, slots=True)
class BarBatchData:
    gateway_name: str
    inst_id: str
    bar: str
    candles: tuple[Candle, ...]
    observed_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class TickBatchData:
    gateway_name: str
    inst_type: str
    ticks: tuple[dict, ...]
    observed_at: datetime = field(default_factory=utc_now)
