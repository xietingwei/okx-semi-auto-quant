from __future__ import annotations

from datetime import datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from urllib.parse import parse_qs, urlparse

from qis.deep_analysis import (
    DEEP_ANALYSIS_MAX_DAYS,
    DeepAnalysisEngine,
    fetch_deep_news,
    rank_deep_analyses,
)
from qis.forecast_learning import apply_strategy_adjustments, hour_bucket
from qis.models import Candle
from qis.okx import OkxClient, OkxError
from qis.position_risk import analyze_position
from qis.spot_dashboard import render_spot_dashboard_cache
from qis.spot_forecast import SpotForecastEngine
from qis.storage import Storage


CANDLE_RANGE_SPECS = {
    "1D": {"bar": "5m", "days": 1, "limit": 288},
    "1M": {"bar": "4H", "days": 31, "limit": 186},
    "3M": {"bar": "12H", "days": 93, "limit": 186},
    "6M": {"bar": "1D", "days": 186, "limit": 186},
    "1Y": {"bar": "1D", "days": 366, "limit": 366},
    "ALL": {"bar": "1D", "days": None, "limit": 1_500},
}

# A custom hourly request is used by the professional chart when the user
# needs a short intraday view. Keep the default bounded to one week; callers
# asking for a longer range should use the named range specs above.
CANDLE_BAR_LIMITS = {
    "5m": 288,
    "15m": 288,
    "30m": 168,
    "1H": 168,
    "2H": 168,
    "4H": 168,
    "6H": 168,
    "12H": 168,
}

CANDLE_BAR_HOURS = {
    "5m": 5 / 60,
    "15m": 0.25,
    "30m": 0.5,
    "1H": 1.0,
    "2H": 2.0,
    "4H": 4.0,
    "6H": 6.0,
    "12H": 12.0,
    "1D": 24.0,
    "1W": 168.0,
}


class LiveQuoteService:
    def __init__(self, ttl_seconds: float = 2.0) -> None:
        self.ttl_seconds = ttl_seconds
        self._updated = 0.0
        self._quotes: dict[str, dict] = {}
        self._lock = threading.Lock()
        threading.Thread(target=self._run, daemon=True, name="qis-live-quotes").start()

    def quotes(self) -> dict[str, dict]:
        with self._lock:
            return dict(self._quotes)

    def _run(self) -> None:
        while True:
            for inst_type in ("SPOT", "SWAP"):
                try:
                    rows = OkxClient().public_tickers(inst_type)
                except OkxError:
                    continue
                if rows:
                    with self._lock:
                        self._quotes.update(
                            {
                                str(item["instId"]): item
                                for item in rows
                                if item.get("instId")
                            }
                        )
                        self._updated = time.monotonic()
            time.sleep(self.ttl_seconds)


class QisRequestHandler(SimpleHTTPRequestHandler):
    def __init__(
        self,
        *args,
        directory: str,
        storage: Storage,
        quote_service: LiveQuoteService,
        **kwargs,
    ) -> None:
        self.storage = storage
        self.quote_service = quote_service
        super().__init__(*args, directory=directory, **kwargs)

    def end_headers(self) -> None:
        if urlparse(self.path).path.endswith((".html", "/")):
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        super().end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self.send_response(302)
            self.send_header("Location", "/index.html")
            self.end_headers()
            return
        if path == "/api/spot/positions":
            positions = [dict(row) for row in self.storage.spot_positions()]
            forecasts = self._position_forecasts(positions)
            analyses = self._position_analyses(positions, forecasts)
            self._json(
                {
                    "positions": positions,
                    "analyses": analyses,
                }
            )
            return
        if path == "/api/spot/quotes":
            forecasts = self._live_forecasts()
            self._json({"forecasts": list(forecasts.values())})
            return
        if path == "/api/spot/candles":
            query = parse_qs(urlparse(self.path).query)
            inst_id = str((query.get("inst_id") or [""])[0])
            range_key = str((query.get("range") or [""])[0]).upper()
            if range_key and range_key not in CANDLE_RANGE_SPECS:
                self._json({"ok": False, "error": "unsupported candle range"}, 400)
                return
            range_spec = CANDLE_RANGE_SPECS.get(range_key)
            requested_bar = str((query.get("bar") or [""])[0])
            normalized_requested_bar = _normalize_candle_bar(requested_bar) if requested_bar else ""
            # A named range supplies a sensible OKX-style default interval, but
            # the chart can explicitly override it (for example 1D + 1H).
            # Keeping the override server-side prevents the UI from showing an
            # hourly label while the API silently returns the default 5m/4H
            # series.
            bar = normalized_requested_bar or (str(range_spec["bar"]) if range_spec else "1D")
            if not bar:
                self._json({"ok": False, "error": "unsupported candle interval"}, 400)
                return
            forecasts = self._forecasts()
            forecast = forecasts.get(inst_id)
            if forecast is None:
                self._json({"ok": False, "error": "unknown instrument"}, 404)
                return
            analysis_forecast = (
                _deep_analysis_forecast(forecast, forecasts)
                if range_spec or bar == "1D"
                else forecast
            )
            cached_candles = _forecast_history_candles(analysis_forecast)
            if _is_external_equity_history(analysis_forecast):
                # Yahoo-backed equities have daily candles only. A raw
                # intraday ``bar`` request must fail explicitly instead of
                # silently returning daily rows labelled as hourly data.
                # Named history ranges use the range's internal interval as a
                # crypto default; for equities they still mean daily history.
                # Only an explicit intraday bar (or the default intraday 1D
                # view) is rejected.
                if normalized_requested_bar and bar != "1D":
                    self._json(
                        {
                            "ok": False,
                            "error": "intraday candles unavailable for external equity",
                        },
                        400,
                    )
                    return
                if not normalized_requested_bar and range_key == "1D":
                    self._json(
                        {
                            "ok": False,
                            "error": "intraday candles unavailable for external equity",
                        },
                        400,
                    )
                    return
                if not cached_candles:
                    self._json({"ok": False, "error": "no cached daily candles"}, 503)
                    return
                candles = _candle_window(
                    cached_candles,
                    range_spec.get("days") if range_spec else None,
                )
                self._json(
                    {
                        "ok": True,
                        "inst_id": inst_id,
                        "bar": "1D",
                        "range": range_key or "ALL",
                        "source": str(
                            analysis_forecast.get("data_source")
                            or analysis_forecast.get("quote_source")
                            or "cached daily history"
                        ),
                        "coverage": len(candles),
                        "analysis_source_inst_id": analysis_forecast.get("analysis_source_inst_id"),
                        "candles": _serialize_candles(candles),
                        "degraded": False,
                        **_candle_span(candles),
                    },
                )
                return
            limit = _candle_limit(range_spec, bar, bool(normalized_requested_bar))
            degraded = False
            try:
                client = OkxClient()
                range_fetcher = getattr(client, "public_range_candles", None)
                if (range_spec or limit > 300) and callable(range_fetcher):
                    # Fetch the live edge plus paginated history. This keeps
                    # the chart current even though history-candles can lag.
                    okx_candles = range_fetcher(inst_id, bar, limit=limit)
                elif range_spec or normalized_requested_bar:
                    # Compatibility fallback for light-weight test clients and
                    # older integrations that only expose history-candles.
                    history_fetcher = getattr(client, "public_history_candles", None)
                    if callable(history_fetcher):
                        okx_candles = history_fetcher(inst_id, bar, limit=limit)
                    else:
                        okx_candles = client.public_candles(
                            inst_id,
                            bar,
                            limit=min(limit, 300),
                        )
                elif limit > 300 and callable(range_fetcher):
                    okx_candles = range_fetcher(inst_id, bar, limit=limit)
                else:
                    okx_candles = client.public_candles(inst_id, bar, limit=limit)
            except OkxError as exc:
                if bar != "1D" or not cached_candles:
                    self._json({"ok": False, "error": str(exc)}, 503)
                    return
                okx_candles = []
                degraded = True
                source = f"{analysis_forecast.get('quote_source') or 'cached daily history'} · OKX unavailable"
            else:
                source = "OKX market candles"
            candles = (
                _merge_candles(cached_candles, okx_candles)
                if bar == "1D"
                else okx_candles
            )
            if range_spec:
                candles = _candle_window(candles, range_spec.get("days"))
            self._json(
                {
                    "ok": True,
                    "inst_id": inst_id,
                    "bar": bar,
                    "range": range_key or "",
                    "source": source,
                    "coverage": len(candles),
                    "candles": _serialize_candles(candles),
                    "degraded": degraded,
                    "warning": "OKX unavailable; using cached daily history" if degraded else "",
                    **_candle_span(candles),
                }
            )
            return
        if path == "/api/deep-analysis/rank":
            query = parse_qs(urlparse(self.path).query)
            try:
                days = int((query.get("days") or [str(DEEP_ANALYSIS_MAX_DAYS)])[0])
            except ValueError:
                self._json({"ok": False, "error": "invalid days"}, 400)
                return
            forecasts = self._live_forecasts()
            ranking = rank_deep_analyses(
                [
                    _deep_analysis_forecast(forecast, forecasts)
                    for forecast in forecasts.values()
                ],
                max_days=days,
            )
            self._json({"ok": True, "ranking": ranking})
            return
        if path == "/api/deep-analysis":
            query = parse_qs(urlparse(self.path).query)
            inst_id = str((query.get("inst_id") or [""])[0])
            try:
                days = int((query.get("days") or [str(DEEP_ANALYSIS_MAX_DAYS)])[0])
            except ValueError:
                self._json({"ok": False, "error": "invalid days"}, 400)
                return
            forecasts = self._live_forecasts() if inst_id else {}
            forecast = forecasts.get(inst_id)
            if forecast is None:
                self._json({"ok": False, "error": "unknown instrument"}, 404)
                return
            analysis_forecast = _deep_analysis_forecast(forecast, forecasts)
            news = fetch_deep_news(inst_id)
            try:
                analysis = DeepAnalysisEngine().analyze(
                    analysis_forecast,
                    news=news,
                    max_days=days,
                )
            except ValueError as exc:
                self._json({"ok": False, "error": str(exc)}, 422)
                return
            self._json({"ok": True, "analysis": analysis})
            return
        if path == "/api/health":
            self._json({"ok": True, "mode": "spot-analysis"})
            return
        super().do_GET()

    def do_POST(self) -> None:
        try:
            payload = self._read_json()
            path = urlparse(self.path).path
            if path == "/api/spot/buy":
                inst_id = str(payload["inst_id"])
                if inst_id not in self._forecasts():
                    raise ValueError("只能登记机会雷达中的股票或加密货币")
                position_id = self.storage.open_spot_position(
                    inst_id=inst_id,
                    buy_price=_positive(payload["buy_price"], "buy_price"),
                    quantity=_positive(payload["quantity"], "quantity"),
                    horizon=str(payload["horizon"]),
                    forecast_return=float(payload["forecast_return"]),
                    up_probability=float(payload["up_probability"]),
                    confidence=float(payload["confidence"]),
                    target_price=_positive(payload["target_price"], "target_price"),
                    notes=str(payload.get("notes", ""))[:500],
                )
                self._json({"ok": True, "id": position_id}, 201)
                return
            if path == "/api/spot/sell":
                success = self.storage.close_spot_position(
                    int(payload["id"]),
                    _positive(payload["sell_price"], "sell_price"),
                )
                self._json(
                    {"ok": success, "error": None if success else "position not found or already closed"},
                    200 if success else 404,
                )
                return
            if path == "/api/spot/delete":
                success = self.storage.delete_spot_position(int(payload["id"]))
                self._json(
                    {"ok": success, "error": None if success else "position not found"},
                    200 if success else 404,
                )
                return
            self._json({"ok": False, "error": "not found"}, 404)
        except (KeyError, TypeError, ValueError) as exc:
            self._json({"ok": False, "error": str(exc)}, 400)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= 100_000:
            raise ValueError("invalid request body")
        return json.loads(self.rfile.read(length).decode())

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _forecasts(self) -> dict[str, dict]:
        path = Path(self.directory) / "spot_forecasts.json"
        if not path.exists():
            return {}
        rows = json.loads(path.read_text(encoding="utf-8"))
        return {str(item["inst_id"]): item for item in rows}

    def _position_forecasts(self, positions: list[dict]) -> dict[str, dict]:
        inst_ids = {
            str(position["inst_id"])
            for position in positions
            if position.get("sell_time") is None
        }
        return self._live_forecasts(inst_ids)

    def _position_analyses(
        self,
        positions: list[dict],
        forecasts: dict[str, dict],
    ) -> list[dict]:
        return [
            analyze_position(position, forecasts[str(position["inst_id"])])
            for position in positions
            if position.get("sell_time") is None
            and str(position["inst_id"]) in forecasts
        ]

    def _live_forecasts(
        self,
        inst_ids: set[str] | None = None,
        *,
        recompute_signals: bool = False,
    ) -> dict[str, dict]:
        forecasts = self._forecasts()
        if inst_ids is not None:
            if not inst_ids:
                return {}
            forecasts = {
                inst_id: forecast
                for inst_id, forecast in forecasts.items()
                if inst_id in inst_ids
            }
        quotes = self.quote_service.quotes()
        adjustment_cache: dict[str, dict[str, dict]] = {}
        result = {}
        for inst_id, forecast in forecasts.items():
            rebased = (
                _rebase_forecast(forecast, quotes.get(inst_id))
                if recompute_signals
                else _rebase_forecast_prices(forecast, quotes.get(inst_id))
            )
            result[inst_id] = self._apply_cached_adjustments(rebased, adjustment_cache)
        return result

    def _apply_cached_adjustments(
        self,
        forecast: dict,
        cache: dict[str, dict[str, dict]],
    ) -> dict:
        def adjustments_for(model_version: str) -> dict[str, dict]:
            key = model_version or "__default__"
            if key not in cache:
                cache[key] = (
                    self.storage.forecast_strategy_adjustments(model_version=model_version)
                    if model_version
                    else self.storage.forecast_strategy_adjustments()
                )
            return cache[key]

        forecast_body = {**forecast}
        variants = list(forecast_body.pop("strategy_variants", []) or [])
        calibrated = apply_strategy_adjustments(
            forecast_body,
            adjustments_for(str(forecast.get("model_version") or "")),
        )
        calibrated["strategy_variants"] = [
            apply_strategy_adjustments(
                variant,
                adjustments_for(str(variant.get("model_version") or "")),
            )
            for variant in variants
        ]
        return calibrated


def serve(host: str, port: int, data_dir: Path, db_path: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    render_spot_dashboard_cache(
        data_dir / "spot_forecasts.json",
        data_dir / "index.html",
    )
    storage = Storage(db_path)
    storage.init()
    if storage.latest_forecast_learning_run() is None:
        evaluation = storage.forecast_evaluation()
        adjustments = storage.forecast_strategy_adjustments()
        storage.record_forecast_learning_run(
            hour_bucket(),
            0,
            evaluation,
            adjustments,
            storage.forecast_advice(),
        )
    quote_service = LiveQuoteService()

    def handler(*args, **kwargs):
        return QisRequestHandler(
            *args,
            directory=str(data_dir),
            storage=storage,
            quote_service=quote_service,
            **kwargs,
        )

    server = ThreadingHTTPServer((host, port), handler)
    print(f"QIS spot web server: http://{host}:{port}", flush=True)
    server.serve_forever()


def _positive(value: object, name: str) -> float:
    number = float(value)
    if number <= 0:
        raise ValueError(f"{name} must be positive")
    return number


def _deep_analysis_forecast(forecast: dict, forecasts: dict[str, dict]) -> dict:
    symbol = str(forecast.get("symbol") or "").upper()
    if not symbol:
        return forecast
    current_len = _history_len(forecast)
    alternatives = [
        item
        for item in forecasts.values()
        if item is not forecast
        and str(item.get("inst_id") or "") != str(forecast.get("inst_id") or "")
        and str(item.get("symbol") or "").upper() == symbol
        and _is_external_equity_history(item)
        and _history_len(item) > current_len
    ]
    if not alternatives:
        return forecast
    best = max(alternatives, key=_history_len)
    merged = {**best}
    merged["inst_id"] = forecast.get("inst_id") or best.get("inst_id")
    merged["symbol"] = forecast.get("symbol") or best.get("symbol")
    merged["analysis_source_inst_id"] = best.get("inst_id")
    return merged


def _is_external_equity_history(forecast: dict) -> bool:
    source = str(forecast.get("data_source") or forecast.get("quote_source") or "")
    market_type = str(forecast.get("market_type") or "")
    return market_type == "美股现货" or "Yahoo Finance" in source


def _history_len(forecast: dict) -> int:
    history = forecast.get("history")
    return len(history) if isinstance(history, list) else 0


def _forecast_history_candles(forecast: dict) -> list[Candle]:
    candles: list[Candle] = []
    for item in forecast.get("history") or []:
        if not isinstance(item, dict):
            continue
        try:
            close = float(item["close"])
            candles.append(
                Candle(
                    ts=_parse_candle_time(item["date"]),
                    open=float(item.get("open", close)),
                    high=float(item.get("high", close)),
                    low=float(item.get("low", close)),
                    close=close,
                    volume=float(item.get("volume", 0) or 0),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(candles, key=lambda item: item.ts)


def _parse_candle_time(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _normalize_candle_bar(value: object) -> str:
    aliases = {
        "5M": "5m",
        "15M": "15m",
        "30M": "30m",
        "1H": "1H",
        "2H": "2H",
        "4H": "4H",
        "6H": "6H",
        "12H": "12H",
        "1D": "1D",
        "1W": "1W",
    }
    return aliases.get(str(value).upper(), "")


def _candle_limit(
    range_spec: dict | None,
    bar: str,
    explicit_bar: bool,
) -> int:
    """Choose a bounded request size for a range/interval pair.

    The range defaults preserve the established terminal behaviour (1D uses
    5m, 1M uses 4H, etc.).  An explicit interval should still cover the full
    selected range, capped at OKX's paginated history ceiling.
    """
    if not range_spec:
        return CANDLE_BAR_LIMITS.get(bar, 300)
    if not explicit_bar:
        return int(range_spec["limit"])
    days = range_spec.get("days")
    if days is None:
        return min(2_000, CANDLE_BAR_LIMITS.get(bar, 300))
    hours = CANDLE_BAR_HOURS.get(bar)
    if not hours or hours <= 0:
        return int(range_spec["limit"])
    return max(1, min(2_000, int(days * 24 / hours + 1)))


def _candle_window(candles: list[Candle], days: int | None) -> list[Candle]:
    ordered = sorted(candles, key=lambda item: item.ts)
    if not ordered or days is None:
        return ordered
    cutoff = ordered[-1].ts - timedelta(days=days)
    return [item for item in ordered if item.ts >= cutoff]


def _candle_span(candles: list[Candle]) -> dict[str, str]:
    if not candles:
        return {"from": "", "to": ""}
    ordered = sorted(candles, key=lambda item: item.ts)
    return {"from": ordered[0].ts.isoformat(), "to": ordered[-1].ts.isoformat()}


def _merge_candles(fallback: list[Candle], primary: list[Candle]) -> list[Candle]:
    # Use the exact interval timestamp. Date-level de-duplication silently
    # discards 23 of 24 hourly candles when this helper is reused for an
    # intraday response.
    by_ts = {
        int(item.ts.timestamp() * 1000): item
        for item in fallback
    }
    by_ts.update(
        {
            int(item.ts.timestamp() * 1000): item
            for item in primary
        }
    )
    return sorted(by_ts.values(), key=lambda item: item.ts)


def _serialize_candles(candles: list[Candle]) -> list[dict]:
    return [
        {
            "date": item.ts.isoformat(),
            "open": item.open,
            "high": item.high,
            "low": item.low,
            "close": item.close,
            "volume": item.volume,
        }
        for item in candles
    ]


def _rebase_forecast_prices(
    forecast: dict,
    quote: dict | None,
    *,
    include_variants: bool = True,
) -> dict:
    if not quote:
        return forecast
    try:
        live_price = float(quote.get("last") or 0)
        base_price = float(forecast["current_price"])
    except (KeyError, TypeError, ValueError):
        return forecast
    if live_price <= 0 or base_price <= 0:
        return forecast
    quote_time = (
        datetime.fromtimestamp(
            int(quote["ts"]) / 1000,
            tz=timezone.utc,
        )
        if quote.get("ts")
        else datetime.now(timezone.utc)
    )
    result = {**forecast}
    result["current_price"] = live_price
    result["forecast_base_price"] = live_price
    result["quote_time"] = quote_time.isoformat()
    result["quote_source"] = "OKX ticker · 5秒动态基准"
    try:
        open_24h = float(quote.get("open24h") or 0)
        if open_24h > 0:
            result["daily_change"] = live_price / open_24h - 1
    except (TypeError, ValueError):
        pass
    for key in ("buy_zone_low", "buy_zone_high", "invalidation"):
        if key in forecast:
            result[key] = live_price * float(forecast[key]) / base_price
    if "forecasts" in forecast:
        result["forecasts"] = []
        for original in forecast["forecasts"]:
            item = {**original}
            item["target"] = live_price * (1 + float(original["expected_return"]))
            item["low"] = live_price * float(original["low"]) / base_price
            item["high"] = live_price * float(original["high"]) / base_price
            result["forecasts"].append(item)
    if include_variants and forecast.get("strategy_variants"):
        result["strategy_variants"] = [
            _rebase_forecast_prices(variant, quote, include_variants=False)
            for variant in forecast["strategy_variants"]
        ]
    return result


def _rebase_forecast(forecast: dict, quote: dict | None) -> dict:
    if not quote:
        return forecast
    try:
        live_price = float(quote.get("last") or 0)
    except (TypeError, ValueError):
        return forecast
    if live_price <= 0:
        return forecast
    base_price = float(forecast["current_price"])
    if base_price <= 0:
        return forecast
    quote_time = (
        datetime.fromtimestamp(
            int(quote["ts"]) / 1000,
            tz=timezone.utc,
        )
        if quote.get("ts")
        else datetime.now(timezone.utc)
    )
    history = forecast.get("history") or []
    if len(history) >= 90:
        candles = [
            Candle(
                ts=datetime.fromisoformat(str(item["date"]).replace("Z", "+00:00")),
                open=float(item.get("open", item["close"])),
                high=float(item.get("high", item["close"])),
                low=float(item.get("low", item["close"])),
                close=float(item["close"]),
                volume=float(item.get("volume", 0)),
            )
            for item in history
        ]
        candles.append(
            Candle(
                ts=quote_time,
                open=live_price,
                high=live_price,
                low=live_price,
                close=live_price,
                volume=0.0,
            )
        )
        engine = SpotForecastEngine()
        refreshed = engine.analyze(
            str(forecast["inst_id"]),
            candles,
            live_price=live_price,
            quote_time=quote_time,
            market_context=forecast.get("market_context") or {},
        )
        if refreshed is not None:
            from dataclasses import asdict

            result = asdict(refreshed)
            result["strategy_variants"] = engine.analyze_suite(
                str(forecast["inst_id"]),
                candles,
                live_price=live_price,
                quote_time=quote_time,
                market_context=forecast.get("market_context") or {},
            )
            result["forecast_base_price"] = live_price
            result["quote_source"] = "OKX ticker · 实时特征重算"
            return result
    result = {**forecast}
    result["current_price"] = live_price
    result["forecast_base_price"] = live_price
    result["quote_time"] = quote_time.isoformat()
    result["quote_source"] = "OKX ticker · 5秒动态基准"
    try:
        open_24h = float(quote.get("open24h") or 0)
        if open_24h > 0:
            result["daily_change"] = live_price / open_24h - 1
    except (TypeError, ValueError):
        pass
    for key in ("buy_zone_low", "buy_zone_high", "invalidation"):
        result[key] = live_price * float(forecast[key]) / base_price
    result["forecasts"] = []
    for original in forecast["forecasts"]:
        item = {**original}
        item["target"] = live_price * (1 + float(original["expected_return"]))
        item["low"] = live_price * float(original["low"]) / base_price
        item["high"] = live_price * float(original["high"]) / base_price
        result["forecasts"].append(item)
    return result
