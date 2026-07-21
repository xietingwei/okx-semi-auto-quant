from datetime import datetime, timedelta, timezone

from qis.models import Candle
from qis.okx import OkxClient


def test_infer_inst_type() -> None:
    assert OkxClient._infer_inst_type("BTC-USDT-SWAP") == "SWAP"
    assert OkxClient._infer_inst_type("BTC-USDT") == "SPOT"


def test_contract_size_from_base_rounds_down() -> None:
    assert OkxClient.contract_size_from_base(0.027525, "0.01", "1", "1") == "2"
    assert OkxClient.contract_size_from_base(1.234, "0.1", "0.1", "0.1") == "12.3"


def test_public_history_candles_pages_backwards_and_deduplicates(monkeypatch) -> None:
    client = OkxClient()
    latest = datetime(2026, 7, 20, tzinfo=timezone.utc)
    rows = []
    for index in range(301):
        timestamp = int((latest - timedelta(days=index)).timestamp() * 1000)
        rows.append([str(timestamp), "100", "103", "98", "101", "1000"])
    calls = []

    def fake_request(method, path, params, auth):
        calls.append((method, path, params, auth))
        if len(calls) == 1:
            return rows[:300]
        return rows[299:301]

    monkeypatch.setattr(client, "_request", fake_request)

    candles = client.public_history_candles("BTC-USDT", "1D", limit=301)

    assert len(candles) == 301
    assert candles[0].ts == latest - timedelta(days=300)
    assert candles[-1].ts == latest
    assert calls[0][1] == "/api/v5/market/history-candles"
    assert calls[0][2]["limit"] == "300"
    assert calls[1][2]["after"] == str(int((latest - timedelta(days=299)).timestamp() * 1000))


def test_public_range_candles_merges_live_edge_with_history(monkeypatch) -> None:
    client = OkxClient()
    start = datetime(2026, 7, 20, tzinfo=timezone.utc)
    latest = [
        Candle(start + timedelta(hours=index), 100, 101, 99, 100.5, 10)
        for index in (2, 3, 4)
    ]
    # History includes an overlap at hour 2 and older rows. The live endpoint
    # must win for the overlap while exact timestamps preserve hourly rows.
    history = [
        Candle(start + timedelta(hours=index), 90, 91, 89, 90.5, 9)
        for index in (0, 1, 2)
    ]
    monkeypatch.setattr(client, "public_candles", lambda inst_id, bar, limit: latest)
    monkeypatch.setattr(client, "public_history_candles", lambda inst_id, bar, limit: history)

    candles = client.public_range_candles("BTC-USDT", "1H", limit=5)

    assert [item.ts for item in candles] == [
        start + timedelta(hours=index) for index in range(5)
    ]
    assert candles[2].close == latest[0].close


def test_parse_candles_skips_malformed_and_non_finite_rows() -> None:
    rows = [
        ["1000", "1", "2", "0.5", "1.5", "10"],
        ["not-a-ts", "1", "2", "0.5", "1.5", "10"],
        ["2000", "nan", "2", "0.5", "1.5", "10"],
        ["3000", "1", "2", "0.5", "1.5", ""],
    ]

    candles = OkxClient._parse_candles(rows)

    assert len(candles) == 2
    assert candles[-1].volume == 0
