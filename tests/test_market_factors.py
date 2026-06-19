from datetime import datetime, timedelta, timezone

from qis.macro import MacroRegime
from qis.market_factors import market_context
from qis.models import Candle
from qis.spot_forecast import SpotForecastEngine


def _candles() -> list[Candle]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        Candle(
            ts=start + timedelta(days=index),
            open=100 + index,
            high=102 + index,
            low=99 + index,
            close=101.5 + index,
            volume=1000 + index * 20,
        )
        for index in range(35)
    ]


def test_market_context_builds_directional_bounded_factors() -> None:
    context = market_context(
        book={
            "bids": [["100", "20", "0", "2"]],
            "asks": [["101", "5", "0", "1"]],
        },
        funding={"fundingRate": "0.001"},
        ticker={"last": "140", "open24h": "135", "bidPx": "139.9", "askPx": "140.1"},
        candles=_candles(),
        macro=MacroRegime("risk_off", -0.6, {"vix_5d": 0.1}, "test"),
        open_interest=1_100,
        open_interest_change=0.10,
        open_interest_history_available=True,
    )

    assert 0 < context["orderbook_score"] <= 1
    assert -1 <= context["funding_score"] < 0
    assert 0 < context["open_interest_score"] <= 1
    assert 0 < context["volume_score"] <= 1
    assert context["macro_score"] == -0.6
    assert all(-1 <= context[key] <= 1 for key in (
        "orderbook_score",
        "funding_score",
        "open_interest_score",
        "volume_score",
        "macro_score",
    ))


def test_factor_weights_use_microstructure_short_and_macro_long() -> None:
    orderbook = {"orderbook_score": 1.0}
    macro = {"macro_score": 1.0}

    _, short_book_delta = SpotForecastEngine._factor_adjustment(7, orderbook)
    _, long_book_delta = SpotForecastEngine._factor_adjustment(180, orderbook)
    _, short_macro_delta = SpotForecastEngine._factor_adjustment(7, macro)
    _, long_macro_delta = SpotForecastEngine._factor_adjustment(180, macro)

    assert short_book_delta > long_book_delta
    assert long_macro_delta > short_macro_delta
