import json

import pytest

from qis.decision_assistant import (
    DecisionAssistant,
    DecisionAssistantError,
    LlmSettings,
    build_decision_context,
)


def test_build_context_links_forecast_position_and_strategy() -> None:
    forecast = {
        "inst_id": "BTC-USDT",
        "current_price": 100,
        "forecasts": [{"key": "1w", "label": "1周", "up_probability": 0.65}],
    }
    context, references = build_decision_context(
        forecasts={"BTC-USDT": forecast},
        selected_inst_id="BTC-USDT",
        selected_horizon="1w",
        positions=[{"id": 7, "inst_id": "BTC-USDT", "sell_time": None}],
        analyses=[{"position_id": 7, "risk_score": 42}],
        evaluation={"overall": {"samples": 30}},
        adjustments={"1w": {"active": True}},
        advice=[{"title": "保持观察"}],
    )

    assert context["selected_asset"]["selected_horizon"]["key"] == "1w"
    assert context["position_risk_analyses"][0]["risk_score"] == 42
    assert any(item["type"] == "strategy" for item in references)


def test_assistant_requires_api_key() -> None:
    assistant = DecisionAssistant(
        LlmSettings("", "https://example.com", "model", "test")
    )

    with pytest.raises(DecisionAssistantError, match="LLM_API_KEY"):
        assistant.ask("现在能买吗？", {})


def test_assistant_calls_openai_compatible_endpoint(monkeypatch) -> None:
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return json.dumps(
                {"choices": [{"message": {"content": "结论：继续观察。"}}]}
            ).encode()

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data.decode())
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assistant = DecisionAssistant(
        LlmSettings("key", "https://llm.example/v1", "qis-model", "custom", 9)
    )

    answer = assistant.ask("现在能买吗？", {"selected_asset": {"inst_id": "BTC-USDT"}})

    assert answer == "结论：继续观察。"
    assert captured["url"] == "https://llm.example/v1/chat/completions"
    assert captured["payload"]["model"] == "qis-model"
    assert captured["timeout"] == 9


def test_assistant_stream_yields_openai_sse_deltas(monkeypatch) -> None:
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def __iter__(self):
            return iter(
                [
                    'data: {"choices":[{"delta":{"content":"结论"}}]}\n'.encode(),
                    'data: {"choices":[{"delta":{"content":"：观察"}}]}\n'.encode(),
                    b"data: [DONE]\n",
                ]
            )

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode())
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assistant = DecisionAssistant(
        LlmSettings("key", "https://llm.example/v1", "qis-model", "custom")
    )

    chunks = list(assistant.ask_stream("现在能买吗？", {}))

    assert chunks == ["结论", "：观察"]
    assert captured["payload"]["stream"] is True
