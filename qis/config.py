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
    us_stock_symbols: tuple[str, ...]
    spot_inst_ids: tuple[str, ...]
    spot_auto_discover: bool
    spot_max_assets: int
    polymarket_enabled: bool
    polymarket_timeout_seconds: int
    polymarket_horizon_days: int
    polymarket_max_events: int
    polymarket_min_liquidity: float
    polymarket_min_volume_24h: float
    polymarket_max_spread: float
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
    min_walk_forward_samples: int
    max_brier_score: float
    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    deepseek_timeout_seconds: int
    deepseek_cache_ttl_seconds: int
    email_alert_enabled: bool
    email_alert_recipients: tuple[str, ...]
    email_alert_score_threshold: int
    email_alert_cooldown_hours: int
    email_smtp_host: str
    email_smtp_port: int
    email_smtp_username: str
    email_smtp_password: str
    email_smtp_from: str
    email_smtp_use_ssl: bool


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
                "",
            ).split(",")
            if item.strip()
        ),
        us_stock_symbols=tuple(
            item.strip().upper()
            for item in os.environ.get(
                "QIS_US_STOCK_SYMBOLS",
                (
                    "AAPL,MSFT,NVDA,AMZN,META,GOOGL,TSLA,AVGO,AMD,NFLX,"
                    "CRM,ORCL,ADBE,COST,JPM,V,MA,UNH,LLY,MRK,"
                    "XOM,CVX,KO,PEP,WMT,HD,MCD,NKE,DIS,INTC"
                ),
            ).split(",")
            if item.strip()
        ),
        spot_inst_ids=tuple(
            item.strip()
            for item in os.environ.get(
                "QIS_SPOT_INST_IDS",
                "BTC-USDT,ETH-USDT,SOL-USDT,XRP-USDT,DOGE-USDT,ADA-USDT,LINK-USDT,AVAX-USDT,BNB-USDT,LTC-USDT",
            ).split(",")
            if item.strip()
        ),
        spot_auto_discover=os.environ.get("QIS_SPOT_AUTO_DISCOVER", "1") == "1",
        spot_max_assets=_int("QIS_SPOT_MAX_ASSETS", 60),
        polymarket_enabled=os.environ.get("QIS_POLYMARKET_ENABLED", "1") == "1",
        polymarket_timeout_seconds=_int("QIS_POLYMARKET_TIMEOUT_SECONDS", 8),
        polymarket_horizon_days=_int("QIS_POLYMARKET_HORIZON_DAYS", 14),
        polymarket_max_events=_int("QIS_POLYMARKET_MAX_EVENTS", 5),
        polymarket_min_liquidity=_float("QIS_POLYMARKET_MIN_LIQUIDITY", 25000),
        polymarket_min_volume_24h=_float("QIS_POLYMARKET_MIN_VOLUME_24H", 10000),
        polymarket_max_spread=_float("QIS_POLYMARKET_MAX_SPREAD", 0.05),
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
        min_walk_forward_samples=_int("QIS_MIN_WALK_FORWARD_SAMPLES", 20),
        max_brier_score=_float("QIS_MAX_BRIER_SCORE", 0.24),
        deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
        deepseek_base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        deepseek_model=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
        deepseek_timeout_seconds=_int("DEEPSEEK_TIMEOUT_SECONDS", 45),
        deepseek_cache_ttl_seconds=_int("DEEPSEEK_CACHE_TTL_SECONDS", 1800),
        email_alert_enabled=os.environ.get("QIS_EMAIL_ALERT_ENABLED", "0") == "1",
        email_alert_recipients=tuple(
            item.strip()
            for item in os.environ.get("QIS_EMAIL_ALERT_RECIPIENTS", "").split(",")
            if item.strip()
        ),
        email_alert_score_threshold=_int("QIS_EMAIL_ALERT_SCORE_THRESHOLD", 85),
        email_alert_cooldown_hours=_int("QIS_EMAIL_ALERT_COOLDOWN_HOURS", 12),
        email_smtp_host=os.environ.get("QIS_EMAIL_SMTP_HOST", "smtp.gmail.com"),
        email_smtp_port=_int("QIS_EMAIL_SMTP_PORT", 465),
        email_smtp_username=os.environ.get("QIS_EMAIL_SMTP_USERNAME", ""),
        email_smtp_password=os.environ.get("QIS_EMAIL_SMTP_PASSWORD", ""),
        email_smtp_from=os.environ.get("QIS_EMAIL_SMTP_FROM", ""),
        email_smtp_use_ssl=os.environ.get("QIS_EMAIL_SMTP_USE_SSL", "1") == "1",
    )
