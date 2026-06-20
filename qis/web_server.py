from __future__ import annotations

from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from urllib.parse import parse_qs, urlparse

from qis.decision_assistant import (
    DecisionAssistant,
    DecisionAssistantError,
    LlmSettings,
    build_decision_context,
)
from qis.forecast_learning import apply_strategy_adjustments, hour_bucket
from qis.models import Candle
from qis.okx import OkxClient, OkxError
from qis.position_risk import analyze_position
from qis.spot_dashboard import render_spot_dashboard_cache
from qis.spot_forecast import SpotForecastEngine
from qis.storage import Storage


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
        assistant: DecisionAssistant,
        **kwargs,
    ) -> None:
        self.storage = storage
        self.quote_service = quote_service
        self.assistant = assistant
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
            forecasts = self._live_forecasts()
            analyses = [
                analyze_position(position, forecasts[position["inst_id"]])
                for position in positions
                if position["sell_time"] is None and position["inst_id"] in forecasts
            ]
            self._json(
                {
                    "positions": positions,
                    "analyses": analyses,
                    "trade_stats": self.storage.spot_trade_stats(),
                    "model_evaluation": self.storage.forecast_evaluation(),
                    "strategy_adjustments": self.storage.forecast_strategy_adjustments(),
                    "advice": self.storage.forecast_advice(),
                    "learning_run": self.storage.latest_forecast_learning_run(),
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
            bar = str((query.get("bar") or ["1H"])[0]).upper()
            if inst_id not in self._forecasts():
                self._json({"ok": False, "error": "unknown instrument"}, 404)
                return
            if bar not in {"1H", "1D"}:
                self._json({"ok": False, "error": "unsupported candle interval"}, 400)
                return
            try:
                candles = OkxClient().public_candles(
                    inst_id,
                    bar,
                    limit=168 if bar == "1H" else 120,
                )
            except OkxError as exc:
                self._json({"ok": False, "error": str(exc)}, 503)
                return
            self._json(
                {
                    "inst_id": inst_id,
                    "bar": bar,
                    "candles": [
                        {
                            "date": item.ts.isoformat(),
                            "open": item.open,
                            "high": item.high,
                            "low": item.low,
                            "close": item.close,
                            "volume": item.volume,
                        }
                        for item in candles
                    ],
                }
            )
            return
        if path == "/api/health":
            self._json(
                {
                    "ok": True,
                    "mode": "spot-analysis",
                    "assistant": self.assistant.status(),
                }
            )
            return
        if path == "/api/assistant/status":
            self._json(self.assistant.status())
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
            if path in {"/api/assistant/ask", "/api/assistant/stream"}:
                forecasts = self._live_forecasts()
                positions = [dict(row) for row in self.storage.spot_positions()]
                analyses = [
                    analyze_position(position, forecasts[position["inst_id"]])
                    for position in positions
                    if position["sell_time"] is None
                    and position["inst_id"] in forecasts
                ]
                evaluation = self.storage.forecast_evaluation()
                adjustments = self.storage.forecast_strategy_adjustments()
                advice = self.storage.forecast_advice()
                learning_run = self.storage.latest_forecast_learning_run()
                context, references = build_decision_context(
                    forecasts=forecasts,
                    selected_inst_id=str(payload.get("inst_id") or ""),
                    selected_horizon=str(payload.get("horizon") or ""),
                    positions=positions,
                    analyses=analyses,
                    evaluation=evaluation,
                    adjustments=adjustments,
                    advice=advice,
                    analysis_scope=str(payload.get("scope") or "asset"),
                    learning_run=learning_run,
                    selected_strategy=str(payload.get("strategy_id") or "adaptive"),
                )
                history = (
                    payload.get("history")
                    if isinstance(payload.get("history"), list)
                    else []
                )
                question = str(payload.get("question") or "")
                if path == "/api/assistant/stream":
                    self._assistant_stream(question, context, history, references)
                else:
                    answer = self.assistant.ask(question, context, history)
                    self._json(
                        {
                            "answer": answer,
                            "references": references,
                            **self.assistant.status(),
                        }
                    )
                return
            self._json({"ok": False, "error": "not found"}, 404)
        except (KeyError, TypeError, ValueError) as exc:
            self._json({"ok": False, "error": str(exc)}, 400)
        except DecisionAssistantError as exc:
            self._json({"ok": False, "error": str(exc)}, 503)

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

    def _assistant_stream(
        self,
        question: str,
        context: dict,
        history: list[dict],
        references: list[dict],
    ) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-transform")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        self._stream_event(
            {
                "type": "start",
                "references": references,
                **self.assistant.status(),
            }
        )
        self._stream_event({"type": "padding", "content": " " * 2048})
        try:
            for content in self.assistant.ask_stream(question, context, history):
                self._stream_event({"type": "delta", "content": content})
            self._stream_event({"type": "done"})
        except (ValueError, DecisionAssistantError) as exc:
            self._stream_event({"type": "error", "error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            return

    def _stream_event(self, payload: dict) -> None:
        body = (
            "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
        ).encode()
        self.wfile.write(body)
        self.wfile.flush()

    def _forecasts(self) -> dict[str, dict]:
        path = Path(self.directory) / "spot_forecasts.json"
        if not path.exists():
            return {}
        rows = json.loads(path.read_text(encoding="utf-8"))
        return {str(item["inst_id"]): item for item in rows}

    def _live_forecasts(self) -> dict[str, dict]:
        forecasts = self._forecasts()
        quotes = self.quote_service.quotes()
        adjustments = self.storage.forecast_strategy_adjustments()
        result = {}
        for inst_id, forecast in forecasts.items():
            rebased = _rebase_forecast(forecast, quotes.get(inst_id))
            variants = rebased.pop("strategy_variants", [])
            calibrated = apply_strategy_adjustments(rebased, adjustments)
            calibrated["strategy_variants"] = [
                apply_strategy_adjustments(
                    variant,
                    self.storage.forecast_strategy_adjustments(
                        model_version=str(variant["model_version"]),
                    ),
                )
                for variant in variants
            ]
            result[inst_id] = calibrated
        return result


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
    assistant = DecisionAssistant(LlmSettings.from_env())

    def handler(*args, **kwargs):
        return QisRequestHandler(
            *args,
            directory=str(data_dir),
            storage=storage,
            quote_service=quote_service,
            assistant=assistant,
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
