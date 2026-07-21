from datetime import datetime, timedelta, timezone
import json

import pytest

from qis.polymarket import (
    PolymarketClient,
    build_asset_intelligence,
    collect_event_catalog,
    map_market_assets,
    normalize_market,
)


NOW = datetime(2026, 7, 21, 2, tzinfo=timezone.utc)


def _market(**overrides) -> dict:
    row = {
        "id": "2758339",
        "question": "Will Bitcoin reach $67,500 in July?",
        "slug": "will-bitcoin-reach-67500-in-july",
        "endDate": (NOW + timedelta(days=10)).isoformat(),
        "outcomes": json.dumps(["Yes", "No"]),
        "outcomePrices": json.dumps(["0.56", "0.44"]),
        "volume24hr": 94_000,
        "liquidityNum": 56_000,
        "bestBid": 0.56,
        "bestAsk": 0.57,
        "spread": 0.01,
        "oneDayPriceChange": 0.09,
        "oneHourPriceChange": -0.02,
        "oneWeekPriceChange": 0.24,
        "active": True,
        "closed": False,
        "events": [{"slug": "bitcoin-price-in-july"}],
    }
    row.update(overrides)
    return row


def test_normalize_market_keeps_real_order_book_fields() -> None:
    event = normalize_market(_market(), now=NOW)

    assert event is not None
    assert event["mapped_symbols"] == ["BTC"]
    assert event["relevance"] == "direct"
    assert event["yes_probability"] == pytest.approx(0.565)
    assert event["spread"] == pytest.approx(0.01)
    assert event["change_24h"] == pytest.approx(0.09)
    assert event["eligible"] is True
    assert event["event_bias"] == "up_event"
    assert event["market_url"].endswith("/bitcoin-price-in-july")


def test_normalize_market_strictly_filters_time_and_market_quality() -> None:
    assert normalize_market(
        _market(endDate=(NOW - timedelta(minutes=1)).isoformat()),
        now=NOW,
    ) is None
    assert normalize_market(
        _market(endDate=(NOW + timedelta(days=15)).isoformat()),
        now=NOW,
    ) is None

    filtered = normalize_market(
        _market(bestBid=0.40, bestAsk=0.50, liquidityNum=100, volume24hr=50),
        now=NOW,
    )
    assert filtered is not None
    assert filtered["eligible"] is False
    assert set(filtered["quality_reasons"]) == {
        "spread_too_wide",
        "low_liquidity",
        "low_volume_24h",
    }

    near_resolved = normalize_market(
        _market(bestBid=0.995, bestAsk=1.0, outcomePrices='["0.999", "0.001"]'),
        now=NOW,
    )
    assert near_resolved is not None
    assert near_resolved["eligible"] is False
    assert "near_resolved" in near_resolved["quality_reasons"]


def test_market_mapping_separates_direct_macro_risk_and_sector() -> None:
    assert map_market_assets("Will Ethereum trade above $4,000?") == (["ETH"], "direct")
    assert map_market_assets("Will the Fed cut interest rates in July?") == ([], "macro")
    assert map_market_assets("Will Israel and Iran agree to a ceasefire?") == ([], "risk")
    assert map_market_assets("Will Brent crude oil exceed $90?") == (["XOM", "CVX"], "sector")


def test_build_asset_intelligence_is_shadow_only_and_asset_specific() -> None:
    btc = normalize_market(_market(), now=NOW)
    fed = normalize_market(
        _market(
            id="fed",
            question="Will the Fed cut interest rates in July?",
            events=[{"slug": "fed-july"}],
        ),
        now=NOW,
    )
    assert btc and fed

    result = build_asset_intelligence(
        [btc, fed],
        ["BTC-USDT", "ETH-USDT"],
        updated_at=NOW,
        snapshot_stats={"capture_windows": 3, "snapshots": 8, "markets": 2},
    )

    assert [event["relevance"] for event in result["BTC-USDT"]["events"]] == [
        "direct",
        "macro",
    ]
    assert [event["relevance"] for event in result["ETH-USDT"]["events"]] == ["macro"]
    assert result["BTC-USDT"]["affects_forecast"] is False
    assert result["BTC-USDT"]["validation"]["affects_forecast"] is False
    assert result["BTC-USDT"]["validation"]["capture_windows"] == 3


def test_direct_event_ladder_prioritizes_decision_relevant_probability() -> None:
    near = normalize_market(_market(id="near"), now=NOW)
    far = normalize_market(
        _market(
            id="far",
            question="Will Bitcoin reach $90,000 in July?",
            bestBid=0.04,
            bestAsk=0.06,
            volume24hr=900_000,
        ),
        now=NOW,
    )
    assert near and far

    result = build_asset_intelligence([far, near], ["BTC-USDT"], updated_at=NOW)

    assert [item["market_id"] for item in result["BTC-USDT"]["events"]] == [
        "near",
        "far",
    ]


def test_collect_catalog_uses_public_client_and_excludes_unrelated_markets() -> None:
    class StubClient(PolymarketClient):
        def active_markets(self, **kwargs):
            return [_market(), _market(id="other", question="Will a movie win an award?")]

    catalog = collect_event_catalog(StubClient(), now=NOW)

    assert [event["market_id"] for event in catalog] == ["2758339"]
