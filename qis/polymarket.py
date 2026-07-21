from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from typing import Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen


POLYMARKET_API_URL = "https://gamma-api.polymarket.com"
POLYMARKET_WEB_URL = "https://polymarket.com/event/"


class PolymarketError(RuntimeError):
    pass


DIRECT_ASSET_PATTERNS: dict[str, tuple[str, ...]] = {
    "BTC": (r"\bBTC\b", r"\bBITCOIN\b"),
    "ETH": (r"\bETH\b", r"\bETHEREUM\b"),
    "SOL": (r"\bSOL\b", r"\bSOLANA\b"),
    "XRP": (r"\bXRP\b", r"\bRIPPLE\b"),
    "DOGE": (r"\bDOGE\b", r"\bDOGECOIN\b"),
    "ADA": (r"\bADA\b", r"\bCARDANO\b"),
    "LINK": (r"\bLINK\b", r"\bCHAINLINK\b"),
    "AVAX": (r"\bAVAX\b", r"\bAVALANCHE\b"),
    "BNB": (r"\bBNB\b", r"\bBINANCE COIN\b"),
    "LTC": (r"\bLTC\b", r"\bLITECOIN\b"),
    "AAPL": (r"\bAAPL\b", r"\bAPPLE(?: INC\.?| STOCK)?\b"),
    "MSFT": (r"\bMSFT\b", r"\bMICROSOFT\b"),
    "NVDA": (r"\bNVDA\b", r"\bNVIDIA\b"),
    "AMZN": (r"\bAMZN\b", r"\bAMAZON\b"),
    "META": (r"\bMETA\b", r"\bFACEBOOK\b"),
    "GOOGL": (r"\bGOOGL?\b", r"\bGOOGLE\b", r"\bALPHABET\b"),
    "TSLA": (r"\bTSLA\b", r"\bTESLA\b"),
    "AVGO": (r"\bAVGO\b", r"\bBROADCOM\b"),
    "AMD": (r"\bAMD\b", r"\bADVANCED MICRO DEVICES\b"),
    "NFLX": (r"\bNFLX\b", r"\bNETFLIX\b"),
    "CRM": (r"\bCRM\b", r"\bSALESFORCE\b"),
    "ORCL": (r"\bORCL\b", r"\bORACLE\b"),
    "ADBE": (r"\bADBE\b", r"\bADOBE\b"),
    "COST": (r"\bCOST\b", r"\bCOSTCO\b"),
    "JPM": (r"\bJPM\b", r"\bJPMORGAN\b", r"\bJP MORGAN\b"),
    "V": (r"\bVISA\b",),
    "MA": (r"\bMASTERCARD\b",),
    "UNH": (r"\bUNH\b", r"\bUNITEDHEALTH\b"),
    "LLY": (r"\bLLY\b", r"\bELI LILLY\b"),
    "MRK": (r"\bMRK\b", r"\bMERCK\b"),
    "XOM": (r"\bXOM\b", r"\bEXXON(?:MOBIL)?\b"),
    "CVX": (r"\bCVX\b", r"\bCHEVRON\b"),
    "KO": (r"\bCOCA-COLA\b", r"\bCOCA COLA\b"),
    "PEP": (r"\bPEP\b", r"\bPEPSICO\b"),
    "WMT": (r"\bWMT\b", r"\bWALMART\b"),
    "HD": (r"\bHOME DEPOT\b",),
    "MCD": (r"\bMCD\b", r"\bMCDONALD(?:'S|S)?\b"),
    "NKE": (r"\bNKE\b", r"\bNIKE\b"),
    "DIS": (r"\bDIS\b", r"\bDISNEY\b"),
    "INTC": (r"\bINTC\b", r"\bINTEL\b"),
}

MACRO_PATTERN = re.compile(
    r"\b(FED(?:ERAL RESERVE)?|FOMC|INTEREST RATES?|RATE CUTS?|RATE HIKES?|"
    r"CPI|INFLATION|RECESSION|UNEMPLOYMENT|NONFARM|GDP)\b",
    re.IGNORECASE,
)
RISK_PATTERN = re.compile(
    r"\b(WAR|CEASEFIRE|IRAN|ISRAEL|RUSSIA|UKRAINE|INVASION|INVADE|"
    r"GEOPOLITICAL|SANCTIONS?|MISSILE|STRAIT OF HORMUZ)\b",
    re.IGNORECASE,
)
ENERGY_PATTERN = re.compile(
    r"\b(OIL|CRUDE|WTI|BRENT|OPEC|GASOLINE|NATURAL GAS)\b",
    re.IGNORECASE,
)
DOWN_EVENT_PATTERN = re.compile(
    r"\b(BELOW|UNDER|DIP(?:S|PED)?|DROP(?:S|PED)?|FALL(?:S|EN)?|LOWER)\b",
    re.IGNORECASE,
)
UP_EVENT_PATTERN = re.compile(
    r"\b(ABOVE|OVER|REACH(?:ES|ED)?|HIT(?:S)?|RISE(?:S)?|HIGHER)\b",
    re.IGNORECASE,
)


class PolymarketClient:
    def __init__(self, timeout_seconds: int = 8) -> None:
        self.timeout_seconds = timeout_seconds

    def active_markets(
        self,
        *,
        now: datetime | None = None,
        horizon_days: int = 14,
        limit: int = 500,
    ) -> list[dict]:
        current = _utc(now)
        query = urlencode(
            {
                "active": "true",
                "closed": "false",
                "limit": max(1, min(500, int(limit))),
                "end_date_min": current.isoformat().replace("+00:00", "Z"),
                "end_date_max": (current + timedelta(days=horizon_days))
                .isoformat()
                .replace("+00:00", "Z"),
                "order": "volume24hr",
                "ascending": "false",
            }
        )
        request = Request(
            f"{POLYMARKET_API_URL}/markets?{query}",
            headers={
                "Accept": "application/json",
                "User-Agent": "QIS-Event-Intelligence/1.0",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise PolymarketError(f"Polymarket public API unavailable: {exc}") from exc
        if not isinstance(payload, list):
            raise PolymarketError("Polymarket returned an invalid market catalog")
        return payload


def collect_event_catalog(
    client: PolymarketClient,
    *,
    now: datetime | None = None,
    horizon_days: int = 14,
    min_liquidity: float = 25_000,
    min_volume_24h: float = 10_000,
    max_spread: float = 0.05,
) -> list[dict]:
    current = _utc(now)
    raw_markets = client.active_markets(now=current, horizon_days=horizon_days)
    catalog = []
    for raw in raw_markets:
        event = normalize_market(
            raw,
            now=current,
            horizon_days=horizon_days,
            min_liquidity=min_liquidity,
            min_volume_24h=min_volume_24h,
            max_spread=max_spread,
        )
        if event is not None:
            catalog.append(event)
    catalog.sort(
        key=lambda item: (
            bool(item["eligible"]),
            float(item["volume_24h"]),
            float(item["liquidity"]),
        ),
        reverse=True,
    )
    return catalog


def normalize_market(
    raw: dict,
    *,
    now: datetime | None = None,
    horizon_days: int = 14,
    min_liquidity: float = 25_000,
    min_volume_24h: float = 10_000,
    max_spread: float = 0.05,
) -> dict | None:
    current = _utc(now)
    question = str(raw.get("question") or "").strip()
    end_at = _parse_time(raw.get("endDate") or raw.get("end_date_iso"))
    if (
        not question
        or end_at is None
        or end_at <= current
        or end_at > current + timedelta(days=horizon_days)
        or raw.get("active") is False
        or bool(raw.get("closed"))
    ):
        return None

    outcomes = _json_list(raw.get("outcomes"))
    prices = _json_list(raw.get("outcomePrices"))
    yes_price = _yes_price(outcomes, prices)
    best_bid = _number(raw.get("bestBid"))
    best_ask = _number(raw.get("bestAsk"))
    if best_bid is not None and best_ask is not None and best_ask >= best_bid:
        yes_probability = (best_bid + best_ask) / 2
        spread = best_ask - best_bid
    else:
        yes_probability = yes_price or _number(raw.get("lastTradePrice"))
        spread = _number(raw.get("spread"))
    yes_probability = (
        max(0.0, min(1.0, yes_probability)) if yes_probability is not None else None
    )
    volume_24h = _number(raw.get("volume24hr")) or 0.0
    liquidity = _number(raw.get("liquidityNum")) or _number(raw.get("liquidity")) or 0.0

    quality_reasons = []
    if yes_probability is None:
        quality_reasons.append("missing_probability")
    elif yes_probability <= 0.01 or yes_probability >= 0.99:
        quality_reasons.append("near_resolved")
    if best_bid is None or best_ask is None or best_ask < best_bid:
        quality_reasons.append("missing_order_book")
    if spread is None or spread > max_spread:
        quality_reasons.append("spread_too_wide")
    if liquidity < min_liquidity:
        quality_reasons.append("low_liquidity")
    if volume_24h < min_volume_24h:
        quality_reasons.append("low_volume_24h")
    eligible = not quality_reasons
    symbols, relevance = map_market_assets(question)
    if not relevance:
        return None

    event_slug = ""
    events = raw.get("events")
    if isinstance(events, list) and events and isinstance(events[0], dict):
        event_slug = str(events[0].get("slug") or "")
    slug = event_slug or str(raw.get("slug") or "")
    event_bias = "non_directional"
    if DOWN_EVENT_PATTERN.search(question):
        event_bias = "down_event"
    elif UP_EVENT_PATTERN.search(question):
        event_bias = "up_event"

    return {
        "market_id": str(raw.get("id") or raw.get("conditionId") or ""),
        "question": question,
        "slug": slug,
        "market_url": f"{POLYMARKET_WEB_URL}{slug}" if slug else "https://polymarket.com",
        "end_at": end_at.isoformat(),
        "yes_probability": yes_probability,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "volume_24h": volume_24h,
        "liquidity": liquidity,
        "change_1h": _number(raw.get("oneHourPriceChange")),
        "change_24h": _number(raw.get("oneDayPriceChange")),
        "change_7d": _number(raw.get("oneWeekPriceChange")),
        "mapped_symbols": symbols,
        "relevance": relevance,
        "event_bias": event_bias,
        "eligible": eligible,
        "quality_state": "qualified" if eligible else "filtered",
        "quality_reasons": quality_reasons,
        "resolution_source": str(raw.get("resolutionSource") or ""),
    }


def map_market_assets(question: str) -> tuple[list[str], str]:
    direct = [
        symbol
        for symbol, patterns in DIRECT_ASSET_PATTERNS.items()
        if any(re.search(pattern, question, re.IGNORECASE) for pattern in patterns)
    ]
    if direct:
        return direct, "direct"
    if ENERGY_PATTERN.search(question):
        return ["XOM", "CVX"], "sector"
    if MACRO_PATTERN.search(question):
        return [], "macro"
    if RISK_PATTERN.search(question):
        return [], "risk"
    return [], ""


def build_asset_intelligence(
    catalog: Iterable[dict],
    inst_ids: Iterable[str],
    *,
    updated_at: datetime | None = None,
    max_events: int = 5,
    snapshot_stats: dict | None = None,
    source_state: str = "live",
    source_error: str = "",
) -> dict[str, dict]:
    current = _utc(updated_at)
    qualified = [item for item in catalog if item.get("eligible")]
    stats = snapshot_stats or {}
    result = {}
    for inst_id in inst_ids:
        symbol = _instrument_symbol(inst_id)
        candidates = [
            item
            for item in qualified
            if (
                symbol in (item.get("mapped_symbols") or [])
                or item.get("relevance") in {"macro", "risk"}
            )
        ]
        candidates.sort(
            key=lambda item: (
                {"direct": 4, "sector": 3, "macro": 2, "risk": 1}.get(
                    str(item.get("relevance")), 0
                ),
                (
                    _decision_information(item.get("yes_probability"))
                    if item.get("relevance") in {"direct", "sector"}
                    else 0.0
                ),
                float(item.get("volume_24h") or 0),
                float(item.get("liquidity") or 0),
            ),
            reverse=True,
        )
        events = candidates[: max(0, max_events)]
        if source_state == "disabled":
            status, state = "disabled", "事件情报未启用"
        elif source_state == "unavailable" and not events:
            status, state = "unavailable", "事件数据暂时不可用"
        elif events:
            status, state = "shadow_observation", "事件证据观察中"
        else:
            status, state = "no_qualified_events", "暂无合格关联事件"
        result[inst_id] = {
            "status": status,
            "state": state,
            "source": "Polymarket public market data",
            "source_state": source_state,
            "source_error": source_error[:240],
            "updated_at": current.isoformat(),
            "event_count": len(events),
            "fetched_count": len(qualified),
            "affects_forecast": False,
            "validation": {
                "state": "影子采集中",
                "capture_windows": int(stats.get("capture_windows") or 0),
                "snapshots": int(stats.get("snapshots") or 0),
                "markets": int(stats.get("markets") or 0),
                "affects_forecast": False,
            },
            "events": events,
        }
    return result


def save_catalog(path: Path, catalog: list[dict], captured_at: datetime | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "captured_at": _utc(captured_at).isoformat(),
                "catalog": catalog,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def load_catalog(path: Path) -> tuple[list[dict], datetime | None]:
    if not path.exists():
        return [], None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        catalog = payload.get("catalog")
        captured_at = _parse_time(payload.get("captured_at"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return [], None
    return (catalog if isinstance(catalog, list) else []), captured_at


def _instrument_symbol(inst_id: str) -> str:
    return str(inst_id).upper().split("-")[0].split(".")[0]


def _yes_price(outcomes: list, prices: list) -> float | None:
    for index, outcome in enumerate(outcomes):
        if str(outcome).strip().lower() == "yes" and index < len(prices):
            return _number(prices[index])
    return _number(prices[0]) if prices else None


def _json_list(value: object) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _decision_information(value: object) -> float:
    probability = _number(value)
    if probability is None:
        return 0.0
    return min(probability, 1.0 - probability)


def _parse_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return _utc(parsed)


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)
