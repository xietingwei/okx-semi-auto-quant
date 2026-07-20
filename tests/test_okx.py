from datetime import datetime, timedelta, timezone

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
