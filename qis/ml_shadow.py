from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from statistics import mean, pstdev
from typing import Any


MODEL_VERSION = "shadow_mlp_v1"
MIN_HISTORY = 90
WINDOW = 30
HORIZON = 5


@dataclass(frozen=True)
class ShadowSample:
    inst_id: str
    features: list[float]
    label: int
    future_return: float
    drawdown: float


class TinyMlp:
    def __init__(self, input_size: int, hidden_size: int = 8) -> None:
        self.input_size = input_size
        self.hidden_size = hidden_size
        self.w1 = [
            [math.sin((i + 1) * (j + 1)) * 0.06 for i in range(input_size)]
            for j in range(hidden_size)
        ]
        self.b1 = [0.0 for _ in range(hidden_size)]
        self.w2 = [math.cos(j + 1) * 0.05 for j in range(hidden_size)]
        self.b2 = 0.0

    def predict(self, features: list[float]) -> float:
        hidden = [
            math.tanh(sum(w * x for w, x in zip(row, features)) + bias)
            for row, bias in zip(self.w1, self.b1)
        ]
        logit = sum(w * h for w, h in zip(self.w2, hidden)) + self.b2
        return _sigmoid(logit)

    def fit(self, samples: list[ShadowSample], *, epochs: int = 120, lr: float = 0.035) -> None:
        if not samples:
            return
        for _ in range(epochs):
            for sample in samples:
                hidden_raw = [
                    sum(w * x for w, x in zip(row, sample.features)) + bias
                    for row, bias in zip(self.w1, self.b1)
                ]
                hidden = [math.tanh(value) for value in hidden_raw]
                pred = _sigmoid(sum(w * h for w, h in zip(self.w2, hidden)) + self.b2)
                error = pred - sample.label
                old_w2 = list(self.w2)
                for index, h in enumerate(hidden):
                    self.w2[index] -= lr * error * h
                self.b2 -= lr * error
                for h_index in range(self.hidden_size):
                    hidden_grad = error * old_w2[h_index] * (1 - hidden[h_index] ** 2)
                    for f_index in range(self.input_size):
                        self.w1[h_index][f_index] -= lr * hidden_grad * sample.features[f_index]
                    self.b1[h_index] -= lr * hidden_grad


def attach_shadow_brain(forecasts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    samples_by_inst = {
        str(forecast.get("inst_id") or ""): _samples(forecast)
        for forecast in forecasts
    }
    all_samples = [
        sample
        for samples in samples_by_inst.values()
        for sample in samples
    ]
    train, validation = _split_samples(all_samples)
    model = TinyMlp(input_size=10)
    model.fit(train)
    global_quality = _validation_quality(model, validation)
    result = []
    for forecast in forecasts:
        item = {**forecast}
        inst_id = str(item.get("inst_id") or "")
        candles = _history(item)
        samples = samples_by_inst.get(inst_id, [])
        if len(candles) < MIN_HISTORY or not samples:
            item["shadow_brain"] = _blocked("至少需要 90 根日K")
        else:
            features = _features(candles[-WINDOW:])
            probability = model.predict(features)
            item["shadow_brain"] = _prediction_payload(
                item,
                probability,
                samples,
                global_quality,
            )
        result.append(item)
    return result


def rank_shadow_brains(forecasts: list[dict[str, Any]]) -> dict[str, Any]:
    ranked = []
    skipped = []
    for forecast in forecasts:
        brain = forecast.get("shadow_brain") or {}
        inst_id = str(forecast.get("inst_id") or "")
        if not brain:
            skipped.append({"inst_id": inst_id, "error": "missing shadow_brain"})
            continue
        row = {
            "rank": 0,
            "inst_id": inst_id,
            "symbol": forecast.get("symbol"),
            "market_type": forecast.get("market_type"),
            "data_source": forecast.get("data_source") or forecast.get("quote_source"),
            "status": brain.get("status"),
            "direction": brain.get("direction"),
            "projection_gate": brain.get("projection_gate"),
            "up_probability": float(brain.get("up_probability") or 0.0),
            "confidence": float(brain.get("confidence") or 0.0),
            "expected_return_5d": float(brain.get("expected_return_5d") or 0.0),
            "validation_accuracy": float(brain.get("validation_accuracy") or 0.0),
            "edge": float(brain.get("edge") or 0.0),
            "samples": int(brain.get("samples") or 0),
            "reason": brain.get("reason") or "",
        }
        row["shadow_score"] = round(_shadow_score(row), 2)
        ranked.append(row)
    ranked.sort(key=lambda row: (row["shadow_score"], row["samples"]), reverse=True)
    for index, row in enumerate(ranked, start=1):
        row["rank"] = index
    return {
        "model_version": MODEL_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(ranked),
        "ranked": ranked,
        "skipped": skipped,
    }


def _samples(forecast: dict[str, Any]) -> list[ShadowSample]:
    inst_id = str(forecast.get("inst_id") or "")
    candles = _history(forecast)
    rows = []
    for index in range(WINDOW, len(candles) - HORIZON):
        current = candles[index - 1]["close"]
        future = candles[index + HORIZON - 1]["close"]
        if current <= 0:
            continue
        forward = future / current - 1
        path = candles[index:index + HORIZON]
        low = min(item["low"] for item in path)
        drawdown = low / current - 1
        rows.append(
            ShadowSample(
                inst_id=inst_id,
                features=_features(candles[index - WINDOW:index]),
                label=1 if forward > 0 else 0,
                future_return=forward,
                drawdown=drawdown,
            )
        )
    return rows


def _history(forecast: dict[str, Any]) -> list[dict[str, float]]:
    rows = []
    for item in forecast.get("history") or []:
        try:
            rows.append(
                {
                    "date": str(item.get("date") or ""),
                    "open": float(item.get("open") or item.get("close") or 0),
                    "high": float(item.get("high") or item.get("close") or 0),
                    "low": float(item.get("low") or item.get("close") or 0),
                    "close": float(item.get("close") or 0),
                    "volume": float(item.get("volume") or 0),
                }
            )
        except (AttributeError, TypeError, ValueError):
            continue
    return [item for item in rows if item["close"] > 0 and item["high"] > 0 and item["low"] > 0]


def _features(candles: list[dict[str, float]]) -> list[float]:
    closes = [item["close"] for item in candles]
    volumes = [item["volume"] for item in candles]
    returns = [
        closes[index] / closes[index - 1] - 1
        for index in range(1, len(closes))
        if closes[index - 1] > 0
    ]
    recent = closes[-1]
    high_20 = max(closes[-20:])
    low_20 = min(closes[-20:])
    avg_volume = mean(volumes[-20:]) if any(volumes[-20:]) else 1.0
    range_position = (recent - low_20) / max(high_20 - low_20, 1e-9)
    range_20 = mean(
        (item["high"] - item["low"]) / max(item["close"], 1e-9)
        for item in candles[-20:]
    )
    return [
        _squash(_return_over(closes, 1), 18),
        _squash(_return_over(closes, 5), 10),
        _squash(_return_over(closes, 20), 5),
        _squash(pstdev(returns[-20:]) if len(returns) > 2 else 0.0, 16),
        _squash(mean(closes[-5:]) / max(mean(closes[-20:]), 1e-9) - 1, 10),
        _squash(mean(closes[-20:]) / max(mean(closes), 1e-9) - 1, 8),
        _squash(volumes[-1] / max(avg_volume, 1e-9) - 1, 1),
        _squash(range_20, 16),
        _squash(recent / max(high_20, 1e-9) - 1, 12),
        max(-1.0, min(1.0, range_position * 2 - 1)),
    ]


def _prediction_payload(
    forecast: dict[str, Any],
    probability: float,
    samples: list[ShadowSample],
    global_quality: dict[str, float],
) -> dict[str, Any]:
    avg_abs_return = mean(abs(item.future_return) for item in samples[-60:]) if samples else 0.0
    expected_return = (probability - 0.5) * max(avg_abs_return * 2.4, 0.01)
    drawdown = abs(mean(item.drawdown for item in samples[-40:])) if samples else 0.0
    confidence = min(1.0, abs(probability - 0.5) * 2 + max(global_quality["edge"], 0) * 1.6)
    direction = "up" if probability >= 0.56 else "down" if probability <= 0.44 else "neutral"
    gate, reason = _gate(confidence, global_quality)
    return {
        "model_version": MODEL_VERSION,
        "status": "shadow_running",
        "direction": direction,
        "up_probability": round(probability, 4),
        "expected_return_5d": round(expected_return, 4),
        "drawdown_risk_5d": round(drawdown, 4),
        "confidence": round(confidence, 4),
        "samples": len(samples),
        "validation_samples": int(global_quality["samples"]),
        "validation_accuracy": round(global_quality["accuracy"], 4),
        "baseline_accuracy": round(global_quality["baseline_accuracy"], 4),
        "edge": round(global_quality["edge"], 4),
        "projection_gate": gate,
        "reason": reason,
        "source": forecast.get("data_source") or forecast.get("quote_source") or "",
    }


def _validation_quality(model: TinyMlp, validation: list[ShadowSample]) -> dict[str, float]:
    if not validation:
        return {"samples": 0.0, "accuracy": 0.0, "baseline_accuracy": 0.0, "edge": 0.0}
    correct = 0
    positives = sum(item.label for item in validation)
    baseline = max(positives, len(validation) - positives) / len(validation)
    for sample in validation:
        prediction = 1 if model.predict(sample.features) >= 0.5 else 0
        if prediction == sample.label:
            correct += 1
    accuracy = correct / len(validation)
    return {
        "samples": float(len(validation)),
        "accuracy": accuracy,
        "baseline_accuracy": baseline,
        "edge": accuracy - baseline,
    }


def _split_samples(samples: list[ShadowSample]) -> tuple[list[ShadowSample], list[ShadowSample]]:
    if len(samples) < 20:
        return samples, []
    split = max(1, int(len(samples) * 0.72))
    return samples[:split], samples[split:]


def _blocked(reason: str) -> dict[str, Any]:
    return {
        "model_version": MODEL_VERSION,
        "status": "insufficient_data",
        "direction": "neutral",
        "up_probability": 0.5,
        "expected_return_5d": 0.0,
        "drawdown_risk_5d": 0.0,
        "confidence": 0.0,
        "samples": 0,
        "validation_samples": 0,
        "validation_accuracy": 0.0,
        "baseline_accuracy": 0.0,
        "edge": 0.0,
        "projection_gate": "blocked",
        "reason": reason,
    }


def _gate(confidence: float, quality: dict[str, float]) -> tuple[str, str]:
    if quality["samples"] < 30:
        return "blocked", "验证样本不足，影子观察"
    if quality["edge"] >= 0.03 and confidence >= 0.55:
        return "allowed", "影子模型相对基准有稳定优势"
    if quality["edge"] >= 0:
        return "watch", "影子运行，优势尚未稳定"
    return "blocked", "近期验证弱于基准，不可推演"


def _shadow_score(row: dict[str, Any]) -> float:
    gate_bonus = {"allowed": 18.0, "watch": 6.0, "blocked": 0.0}.get(
        str(row.get("projection_gate")),
        0.0,
    )
    return (
        gate_bonus
        + float(row.get("confidence") or 0.0) * 34
        + max(0.0, float(row.get("edge") or 0.0)) * 80
        + min(20.0, int(row.get("samples") or 0) / 8)
    )


def _return_over(closes: list[float], days: int) -> float:
    if len(closes) <= days or closes[-days - 1] <= 0:
        return 0.0
    return closes[-1] / closes[-days - 1] - 1


def _squash(value: float, scale: float) -> float:
    return math.tanh(value * scale)


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1 / (1 + z)
    z = math.exp(value)
    return z / (1 + z)
