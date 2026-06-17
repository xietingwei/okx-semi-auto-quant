from datetime import datetime, timedelta, timezone

from qis.analyzer import MarketAnalyzer
from qis.macro import MacroRegime
from qis.models import Candle


def _candles(count: int = 140) -> list[Candle]:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    price = 100.0
    for idx in range(count):
        price += 0.15
        rows.append(
            Candle(
                ts=base + timedelta(minutes=15 * idx),
                open=price - 0.2,
                high=price + 0.6,
                low=price - 0.6,
                close=price,
                volume=1000,
            )
        )
    rows[-2] = Candle(rows[-2].ts, 125.0, 130.0, 124.0, 129.5, 1000)
    return rows


def test_market_analyzer_returns_opportunities() -> None:
    opportunities = MarketAnalyzer().analyze("BTC-USDT-SWAP", _candles())

    assert opportunities
    assert opportunities[0].inst_id == "BTC-USDT-SWAP"
    assert opportunities[0].asset_class == "crypto"
    assert 0 <= opportunities[0].success_probability <= 1
    assert opportunities[0].model == "similarity_bayes_macro_intel_v3"


def test_macro_risk_on_boosts_long_probability() -> None:
    neutral = MarketAnalyzer(macro=MacroRegime("neutral", 0.0, {}, "test")).analyze("BTC-USDT-SWAP", _candles())
    risk_on = MarketAnalyzer(macro=MacroRegime("risk_on", 0.8, {}, "test")).analyze("BTC-USDT-SWAP", _candles())
    neutral_long = next(item for item in neutral if item.side.value == "buy")
    risk_on_long = next(item for item in risk_on if item.side.value == "buy")

    assert risk_on_long.success_probability > neutral_long.success_probability


def test_market_analyzer_marks_stock_assets() -> None:
    opportunities = MarketAnalyzer().analyze("NVDA-USDT-SWAP", _candles())

    assert opportunities[0].asset_class == "stock"
