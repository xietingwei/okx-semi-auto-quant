"""Data quality and scope gates for the short-term forecast.

The forecast engine intentionally works with daily candles.  A long list of
candles is not necessarily a usable history though: duplicated rows, missing
days, or an old last quote can make a 1–14 day signal look more precise than
it is.  This module keeps those checks small, deterministic, and independent
of the HTTP/UI layer so callers can expose the evidence alongside a signal.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from qis.models import Candle


SHORT_TERM_PRIMARY_HORIZON = "3d"
SHORT_TERM_CONFIRMATION_HORIZON = "1w"
SHORT_TERM_MAX_HORIZON = "2w"


def assess_short_term_data(
    candles: Iterable[Candle],
    *,
    as_of: datetime | None = None,
    min_bars: int = 90,
    expected_interval_days: float = 1.0,
    allow_weekends: bool = False,
) -> dict:
    """Return a conservative, serialisable quality report for daily candles.

    ``as_of`` is normally the live quote time.  When absent, the latest candle
    is used, which avoids penalising a historical/backtest call for being old.
    Weekend gaps are allowed (up to 3.5 days for daily data), while larger gaps
    are counted as missing history.  The report never fabricates candles.
    """

    ordered = sorted(
        (item for item in candles if _valid_candle(item)),
        key=lambda item: _aware(item.ts),
    )
    unique: list[Candle] = []
    seen: set[datetime] = set()
    for item in ordered:
        ts = _aware(item.ts)
        if ts in seen:
            continue
        seen.add(ts)
        unique.append(item)

    if not unique:
        return _empty_report(min_bars)

    timestamps = [_aware(item.ts) for item in unique]
    gaps_hours = [
        (right - left).total_seconds() / 3600.0
        for left, right in zip(timestamps, timestamps[1:])
    ]
    # Crypto daily candles are continuous; equity candles can skip a weekend.
    # The wider threshold therefore tolerates a normal non-trading weekend but
    # still catches multi-day API outages.
    allowed_gap_days = (
        max(expected_interval_days * 1.75, 3.5)
        if allow_weekends
        else max(expected_interval_days * 1.75, 2.0)
    )
    gap_hours = [value for value in gaps_hours if value > allowed_gap_days * 24]
    largest_gap_hours = max(gaps_hours, default=0.0)
    first_ts, last_ts = timestamps[0], timestamps[-1]
    span_days = max(0.0, (last_ts - first_ts).total_seconds() / 86400.0)
    reference = _aware(as_of) if as_of is not None else last_ts
    stale_hours = max(0.0, (reference - last_ts).total_seconds() / 3600.0)

    coverage_ratio = min(1.0, len(unique) / max(1, int(min_bars)))
    gap_ratio = len(gap_hours) / max(1, len(unique) - 1)
    # A stale quote is a problem for a short-horizon signal, but it should not
    # instantly turn a Friday equity close into an unusable record on Monday.
    freshness_score = _clip(1.0 - max(0.0, stale_hours - 36.0) / (24.0 * 5.0), 0.0, 1.0)
    score = _clip(
        0.62 * coverage_ratio
        + 0.23 * (1.0 - min(1.0, gap_ratio * 12.0))
        + 0.15 * freshness_score,
        0.0,
        1.0,
    )
    warnings: list[str] = []
    if len(unique) < min_bars:
        warnings.append(f"历史K线不足{min_bars}根")
    if gap_hours:
        warnings.append(f"检测到{len(gap_hours)}处历史缺口")
    if stale_hours > 72.0:
        warnings.append("最新K线距当前超过72小时")
    quality = "A" if score >= 0.86 else "B" if score >= 0.70 else "C" if score >= 0.52 else "D"
    # A high bar count cannot mask structural defects.  Keep the numeric
    # score for diagnostics, but make the displayed grade conservative when
    # an actual gap or insufficient sample depth is present.
    if gap_hours or len(unique) < min_bars:
        quality = "C" if score >= 0.52 else "D"
    elif stale_hours > 72.0 and quality == "A":
        quality = "B"
    actionable = (
        len(unique) >= min_bars
        and not gap_hours
        and stale_hours <= 72.0
        and quality in {"A", "B"}
    )
    return {
        "quality": quality,
        "score": round(score * 100),
        "actionable": actionable,
        "bars": len(unique),
        "duplicate_bars": len(ordered) - len(unique),
        "first": first_ts.isoformat(),
        "last": last_ts.isoformat(),
        "span_days": round(span_days, 2),
        "gap_count": len(gap_hours),
        "largest_gap_hours": round(largest_gap_hours, 2),
        "stale_hours": round(stale_hours, 2),
        "coverage_ratio": round(coverage_ratio, 4),
        "warnings": warnings,
    }


def canonicalize_candles(candles: Iterable[Candle]) -> list[Candle]:
    """Sort candles by time and retain one valid row per timestamp.

    Exchange responses are usually ordered, but pagination and merged cached
    history can introduce duplicate or out-of-order rows.  Forecast features
    and walk-forward origins must consume the same canonical sequence.
    """

    ordered = sorted(
        (item for item in candles if _valid_candle(item)),
        key=lambda item: _aware(item.ts),
    )
    result: list[Candle] = []
    seen: set[datetime] = set()
    for item in ordered:
        timestamp = _aware(item.ts)
        if timestamp in seen:
            continue
        seen.add(timestamp)
        result.append(item)
    return result


def short_term_context(data_quality: dict) -> dict:
    """Describe what the forecast is allowed to claim.

    Keeping this explicit prevents downstream clients from treating the old
    1/3/6-month labels as model horizons when the engine only validates short
    windows.
    """

    if data_quality.get("actionable"):
        state = "可参考"
        reason = "3天定方向，7天确认，最长仅作14天观察"
    else:
        state = "仅观察"
        warnings = data_quality.get("warnings") or ["历史数据质量未通过短线门槛"]
        reason = "；".join(str(item) for item in warnings)
    return {
        "scope": "short_term",
        "primary_horizon": SHORT_TERM_PRIMARY_HORIZON,
        "confirmation_horizon": SHORT_TERM_CONFIRMATION_HORIZON,
        "max_horizon": SHORT_TERM_MAX_HORIZON,
        "state": state,
        "reason": reason,
        "actionable": bool(data_quality.get("actionable")),
    }


def _empty_report(min_bars: int) -> dict:
    return {
        "quality": "D",
        "score": 0,
        "actionable": False,
        "bars": 0,
        "duplicate_bars": 0,
        "first": "",
        "last": "",
        "span_days": 0.0,
        "gap_count": 0,
        "largest_gap_hours": 0.0,
        "stale_hours": None,
        "coverage_ratio": 0.0,
        "warnings": [f"没有可用K线（至少需要{min_bars}根）"],
    }


def _valid_candle(item: object) -> bool:
    try:
        return bool(
            item.ts
            and float(item.close) > 0
            and float(item.low) > 0
            and float(item.high) >= float(item.low)
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
