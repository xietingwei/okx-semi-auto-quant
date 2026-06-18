from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class ValidationResult:
    raw_probability: float
    calibrated_probability: float
    walk_forward_samples: int
    brier_score: float | None
    calibration_error: float | None
    drift_status: str


def similarity_probability(
    current_features: tuple[float, ...],
    events: list[tuple[tuple[float, ...], bool]],
    feature_weights: tuple[float, ...],
    prior_mean: float = 0.48,
    prior_strength: float = 8.0,
) -> ValidationResult:
    raw_probability = _weighted_probability(
        current_features,
        events,
        feature_weights,
        prior_mean,
        prior_strength,
    )
    predictions: list[tuple[float, bool]] = []
    min_history = 8
    for index in range(min_history, len(events)):
        features, outcome = events[index]
        prediction = _weighted_probability(
            features,
            events[:index],
            feature_weights,
            prior_mean,
            prior_strength,
        )
        predictions.append((prediction, outcome))
    if not predictions:
        return ValidationResult(raw_probability, raw_probability, 0, None, None, "insufficient")
    calibrated = _local_calibration(raw_probability, predictions)
    brier = sum((probability - float(outcome)) ** 2 for probability, outcome in predictions) / len(predictions)
    calibration_error = _expected_calibration_error(predictions)
    drift_status = _drift_status(predictions)
    return ValidationResult(
        raw_probability=raw_probability,
        calibrated_probability=calibrated,
        walk_forward_samples=len(predictions),
        brier_score=brier,
        calibration_error=calibration_error,
        drift_status=drift_status,
    )


def _weighted_probability(
    current_features: tuple[float, ...],
    events: list[tuple[tuple[float, ...], bool]],
    feature_weights: tuple[float, ...],
    prior_mean: float,
    prior_strength: float,
) -> float:
    total_weight = 0.0
    weighted_wins = 0.0
    for features, outcome in events:
        distance = math.sqrt(
            sum(weight * (left - right) ** 2 for weight, left, right in zip(feature_weights, current_features, features))
        )
        weight = math.exp(-distance * 0.85)
        total_weight += weight
        weighted_wins += weight if outcome else 0.0
    return (weighted_wins + prior_mean * prior_strength) / (total_weight + prior_strength)


def _local_calibration(raw_probability: float, predictions: list[tuple[float, bool]]) -> float:
    weighted_outcomes = 0.0
    total_weight = 0.0
    for probability, outcome in predictions:
        distance = abs(probability - raw_probability)
        weight = math.exp(-((distance / 0.12) ** 2))
        weighted_outcomes += weight if outcome else 0.0
        total_weight += weight
    calibration_strength = 10.0
    calibrated = (weighted_outcomes + raw_probability * calibration_strength) / (total_weight + calibration_strength)
    return max(0.05, min(0.85, calibrated))


def _expected_calibration_error(predictions: list[tuple[float, bool]], bins: int = 5) -> float:
    total = len(predictions)
    error = 0.0
    for bin_index in range(bins):
        low = bin_index / bins
        high = (bin_index + 1) / bins
        bucket = [
            (probability, outcome)
            for probability, outcome in predictions
            if low <= probability < high or (bin_index == bins - 1 and probability == high)
        ]
        if not bucket:
            continue
        mean_probability = sum(item[0] for item in bucket) / len(bucket)
        observed_rate = sum(1 for _, outcome in bucket if outcome) / len(bucket)
        error += len(bucket) / total * abs(mean_probability - observed_rate)
    return error


def _drift_status(predictions: list[tuple[float, bool]]) -> str:
    if len(predictions) < 20:
        return "insufficient"
    split = max(10, len(predictions) // 3)
    recent = predictions[-split:]
    previous = predictions[:-split]
    if not previous:
        return "insufficient"
    recent_brier = sum((probability - float(outcome)) ** 2 for probability, outcome in recent) / len(recent)
    previous_brier = sum((probability - float(outcome)) ** 2 for probability, outcome in previous) / len(previous)
    if recent_brier > max(0.30, previous_brier * 1.35):
        return "drift"
    if recent_brier > max(0.24, previous_brier * 1.15):
        return "warning"
    return "stable"
