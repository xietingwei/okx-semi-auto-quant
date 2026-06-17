from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from qis.models import Mode


def _load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    okx_api_key: str
    okx_api_secret: str
    okx_api_passphrase: str
    okx_simulated: bool
    mode: Mode
    db_path: Path
    pause_file: Path
    inst_id: str
    inst_ids: tuple[str, ...]
    stock_inst_ids: tuple[str, ...]
    bar: str
    loop_seconds: int
    initial_equity: float
    risk_per_trade: float
    daily_loss_limit: float
    max_drawdown: float
    max_leverage: float
    max_notional_pct: float
    max_trades_per_day: int
    donchian_lookback: int
    atr_period: int
    atr_multiplier: float
    ema_fast: int
    ema_slow: int
    min_success_probability: float


def load_settings() -> Settings:
    _load_dotenv()
    return Settings(
        okx_api_key=os.environ.get("OKX_API_KEY", ""),
        okx_api_secret=os.environ.get("OKX_API_SECRET", ""),
        okx_api_passphrase=os.environ.get("OKX_API_PASSPHRASE", ""),
        okx_simulated=os.environ.get("OKX_SIMULATED", "1") == "1",
        mode=Mode(os.environ.get("QIS_MODE", "paper").lower()),
        db_path=Path(os.environ.get("QIS_DB_PATH", "data/qis.sqlite3")),
        pause_file=Path(os.environ.get("QIS_PAUSE_FILE", "data/PAUSE")),
        inst_id=os.environ.get("QIS_INST_ID", "BTC-USDT-SWAP"),
        inst_ids=tuple(
            item.strip()
            for item in os.environ.get(
                "QIS_INST_IDS",
                (
                    "BTC-USDT-SWAP,ETH-USDT-SWAP,SOL-USDT-SWAP,XRP-USDT-SWAP,"
                    "DOGE-USDT-SWAP,ADA-USDT-SWAP,LINK-USDT-SWAP,AVAX-USDT-SWAP,"
                    "BNB-USDT-SWAP,LTC-USDT-SWAP"
                ),
            ).split(",")
            if item.strip()
        ),
        stock_inst_ids=tuple(
            item.strip()
            for item in os.environ.get(
                "QIS_STOCK_INST_IDS",
                "AAPL-USDT-SWAP,AMZN-USDT-SWAP,GOOGL-USDT-SWAP,META-USDT-SWAP,MSFT-USDT-SWAP,NVDA-USDT-SWAP,TSLA-USDT-SWAP",
            ).split(",")
            if item.strip()
        ),
        bar=os.environ.get("QIS_BAR", "15m"),
        loop_seconds=_int("QIS_LOOP_SECONDS", 60),
        initial_equity=_float("QIS_INITIAL_EQUITY", 5000),
        risk_per_trade=_float("QIS_RISK_PER_TRADE", 0.0075),
        daily_loss_limit=_float("QIS_DAILY_LOSS_LIMIT", 0.025),
        max_drawdown=_float("QIS_MAX_DRAWDOWN", 0.12),
        max_leverage=_float("QIS_MAX_LEVERAGE", 2),
        max_notional_pct=_float("QIS_MAX_NOTIONAL_PCT", 0.35),
        max_trades_per_day=_int("QIS_MAX_TRADES_PER_DAY", 6),
        donchian_lookback=_int("QIS_DONCHIAN_LOOKBACK", 20),
        atr_period=_int("QIS_ATR_PERIOD", 14),
        atr_multiplier=_float("QIS_ATR_MULTIPLIER", 1.8),
        ema_fast=_int("QIS_EMA_FAST", 0),
        ema_slow=_int("QIS_EMA_SLOW", 0),
        min_success_probability=_float("QIS_MIN_SUCCESS_PROBABILITY", 0.70),
    )
