import json

from qis.us_stocks import YahooFinanceClient


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self) -> bytes:
        timestamps = [1_700_000_000 + index * 86_400 for index in range(100)]
        payload = {
            "chart": {
                "result": [
                    {
                        "meta": {
                            "currency": "USD",
                            "symbol": "NVDA",
                            "exchangeName": "NMS",
                        },
                        "timestamp": timestamps,
                        "indicators": {
                            "quote": [
                                {
                                    "open": [100 + index for index in range(100)],
                                    "high": [101 + index for index in range(100)],
                                    "low": [99 + index for index in range(100)],
                                    "close": [100.5 + index for index in range(100)],
                                    "volume": [1_000_000 + index for index in range(100)],
                                }
                            ]
                        },
                    }
                ],
                "error": None,
            }
        }
        return json.dumps(payload).encode()


def test_yahoo_finance_client_maps_daily_history(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        assert "NVDA" in request.full_url
        assert "interval=1d" in request.full_url
        assert timeout == 10
        return _FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    history = YahooFinanceClient().daily_history("nvda")

    assert history.symbol == "NVDA"
    assert history.inst_id == "NVDA-US"
    assert history.exchange == "NMS"
    assert history.quote_source == "Yahoo Finance 日线 · NMS"
    assert "美股券商" in history.trade_platform
    assert len(history.candles) == 100
    assert history.candles[-1].close == 199.5
