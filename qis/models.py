from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class Mode(str, Enum):
    PAPER = "paper"
    LIVE = "live"


@dataclass(frozen=True)
class Candle:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Signal:
    inst_id: str
    side: Side
    entry: float
    stop: float
    take_profit: float | None
    reason: str
    confidence: float
    created_at: datetime


@dataclass(frozen=True)
class AccountState:
    equity: float
    peak_equity: float
    daily_pnl: float
    open_notional: float
    trades_today: int


@dataclass(frozen=True)
class TradePlan:
    signal: Signal
    size: float
    notional: float
    risk_amount: float
    leverage: float
    approved: bool
    reason: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
