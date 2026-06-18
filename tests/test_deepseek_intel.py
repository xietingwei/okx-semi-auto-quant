from pathlib import Path

from qis.deepseek_intel import DeepSeekIntelProvider, DeepSeekSettings
from qis.external_intel import ExternalIntel, Headline


def _provider(tmp_path: Path) -> DeepSeekIntelProvider:
    return DeepSeekIntelProvider(
        DeepSeekSettings(
            api_key="test-key",
            cache_path=tmp_path / "deepseek.json",
        )
    )


def test_deepseek_validation_rejects_unknown_assets(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    payload = {
        "market_summary": "test",
        "global_score": 4,
        "events": [
            {"asset": "BTC", "direction": 1, "impact": 0.8, "confidence": 0.7},
            {"asset": "UNKNOWN", "direction": -1, "impact": 1, "confidence": 1},
        ],
    }

    result = provider._validate(payload, ("BTC-USDT-SWAP",))

    assert result["global_score"] == 1.0
    assert len(result["events"]) == 1
    assert result["events"][0]["asset"] == "BTC"


def test_deepseek_merge_creates_asset_scores(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    intel = ExternalIntel(
        label="mixed",
        score=0.0,
        headlines=[],
        reason="test",
        fetched_at="now",
    )
    research = {
        "market_summary": "BTC positive, NVDA negative",
        "global_score": 0.1,
        "events": [
            {"asset": "BTC", "direction": 1, "impact": 0.8, "confidence": 0.9},
            {"asset": "NVDA", "direction": -1, "impact": 0.7, "confidence": 0.8},
        ],
    }

    enriched = provider._merge(intel, research, "deepseek-v4-flash")

    assert enriched.asset_scores["BTC"] > 0
    assert enriched.asset_scores["NVDA"] < 0
    assert enriched.provider == "deepseek-v4-flash"


def test_balanced_headlines_limits_each_source(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    headlines = [
        Headline(source, f"{source}-{index}", "")
        for source in ("crypto", "stocks")
        for index in range(10)
    ]
    intel = ExternalIntel("mixed", 0.0, headlines, "test", "now")

    selected = provider._balanced_headlines(intel, per_source=3)

    assert len(selected) == 6
