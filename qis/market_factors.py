from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path
from typing import Any

from qis.macro import MacroRegime
from qis.models import Candle
from qis.okx import OkxClient, OkxError


def build_market_contexts(
    client: OkxClient,
    inst_ids: tuple[str, ...],
    ticker_map: dict[str, dict],
    candles_by_inst: dict[str, list[Candle]],
    macro: MacroRegime,
    cache_path: Path,
) -> dict[str, dict]:
    previous = _load_cache(cache_path)
    try:
        open_interest = {
            str(item.get("instId")): float(item.get("oiCcy") or item.get("oi") or 0)
            for item in client.public_open_interest("SWAP")
            if item.get("instId")
        }
    except (OkxError, TypeError, ValueError):
        open_interest = {}

    crypto_ids = [item for item in inst_ids if not item.endswith("-SWAP")]

    def fetch(inst_id: str) -> tuple[str, dict, dict]:
        swap_id = f"{inst_id}-SWAP"
        try:
            book = client.public_order_book(inst_id, 20)
        except OkxError:
            book = {}
        if swap_id in ticker_map:
            try:
                funding = client.public_funding_rate(swap_id)
            except OkxError:
                funding = {}
        else:
            funding = {}
        return inst_id, book, funding

    fetched: dict[str, tuple[dict, dict]] = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        for inst_id, book, funding in executor.map(fetch, crypto_ids):
            fetched[inst_id] = (book, funding)

    contexts: dict[str, dict] = {}
    current_cache: dict[str, float] = {}
    for inst_id in inst_ids:
        swap_id = inst_id if inst_id.endswith("-SWAP") else f"{inst_id}-SWAP"
        book, funding = fetched.get(inst_id, ({}, {}))
        ticker = ticker_map.get(inst_id, {})
        oi = float(open_interest.get(swap_id, 0.0))
        current_cache[swap_id] = oi
        previous_oi = float(previous.get(swap_id, 0.0))
        oi_change = oi / previous_oi - 1 if oi > 0 and previous_oi > 0 else 0.0
        contexts[inst_id] = market_context(
            book=book,
            funding=funding,
            ticker=ticker,
            candles=candles_by_inst.get(inst_id, []),
            macro=macro,
            open_interest=oi,
            open_interest_change=oi_change,
            open_interest_history_available=previous_oi > 0,
        )
    _save_cache(cache_path, current_cache)
    return contexts


def market_context(
    *,
    book: dict,
    funding: dict,
    ticker: dict,
    candles: list[Candle],
    macro: MacroRegime,
    open_interest: float,
    open_interest_change: float,
    open_interest_history_available: bool,
) -> dict:
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    bid_depth = sum(float(row[0]) * float(row[1]) for row in bids)
    ask_depth = sum(float(row[0]) * float(row[1]) for row in asks)
    depth_total = bid_depth + ask_depth
    orderbook_score = (
        (bid_depth - ask_depth) / depth_total if depth_total > 0 else 0.0
    )
    try:
        bid = float(ticker.get("bidPx") or 0)
        ask = float(ticker.get("askPx") or 0)
        mid = (bid + ask) / 2
        spread_bps = (ask - bid) / mid * 10_000 if mid > 0 and ask >= bid else 0.0
    except (TypeError, ValueError):
        spread_bps = 0.0

    funding_rate = _number(funding.get("fundingRate"))
    funding_score = -math.tanh(funding_rate / 0.0005)
    daily_change = _daily_change(ticker, candles)
    oi_score = math.tanh(open_interest_change * 8) * (
        1.0 if daily_change >= 0 else -1.0
    )
    volume_score, volume_ratio = _volume_structure(candles)
    macro_score = max(-1.0, min(1.0, float(macro.risk_score)))
    return {
        "orderbook_score": _clip(orderbook_score),
        "spread_bps": max(0.0, spread_bps),
        "funding_rate": funding_rate,
        "funding_score": _clip(funding_score),
        "open_interest": open_interest,
        "open_interest_change": open_interest_change,
        "open_interest_score": _clip(oi_score),
        "volume_score": _clip(volume_score),
        "volume_ratio": volume_ratio,
        "macro_score": macro_score,
        "macro_label": macro.label,
        "available": {
            "orderbook": bool(bids and asks),
            "funding": bool(funding),
            "open_interest": open_interest > 0 and open_interest_history_available,
            "volume": len(candles) >= 20,
            "macro": bool(macro.components),
        },
    }

def _volume_structure(candles: list[Candle]) -> tuple[float, float]:
    closed = candles[:-1] if len(candles) > 1 else candles
    if len(closed) < 20:
        return 0.0, 1.0
    recent = closed[-10:]
    baseline = closed[-30:] if len(closed) >= 30 else closed
    avg_recent = sum(item.volume for item in recent) / len(recent)
    avg_baseline = sum(item.volume for item in baseline) / len(baseline)
    ratio = avg_recent / avg_baseline if avg_baseline > 0 else 1.0
    signed = sum(
        item.volume * (1 if item.close >= item.open else -1)
        for item in recent
    )
    total = sum(item.volume for item in recent)
    direction = signed / total if total > 0 else 0.0
    participation = math.tanh((ratio - 1.0) * 1.5)
    return direction * (0.65 + 0.35 * max(0.0, participation)), ratio


def _daily_change(ticker: dict, candles: list[Candle]) -> float:
    try:
        last = float(ticker.get("last") or 0)
        open_24h = float(ticker.get("open24h") or 0)
        if last > 0 and open_24h > 0:
            return last / open_24h - 1
    except (TypeError, ValueError):
        pass
    if len(candles) >= 2 and candles[-2].close > 0:
        return candles[-1].close / candles[-2].close - 1
    return 0.0


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _clip(value: float) -> float:
    return max(-1.0, min(1.0, value))


def _load_cache(path: Path) -> dict[str, float]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return {str(key): float(value) for key, value in payload.items()}
    except (OSError, ValueError, TypeError):
        return {}


def _save_cache(path: Path, payload: dict[str, float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
