from __future__ import annotations

from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from urllib.parse import urlparse

from qis.decision_assistant import (
    DecisionAssistant,
    DecisionAssistantError,
    LlmSettings,
    build_decision_context,
)
from qis.forecast_learning import apply_strategy_adjustments
from qis.okx import OkxClient, OkxError
from qis.position_risk import analyze_position
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
                }
            )
            return
        if path == "/api/spot/quotes":
            forecasts = self._live_forecasts()
            self._json({"forecasts": list(forecasts.values())})
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
            if path == "/api/assistant/ask":
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
                context, references = build_decision_context(
                    forecasts=forecasts,
                    selected_inst_id=str(payload.get("inst_id") or ""),
                    selected_horizon=str(payload.get("horizon") or ""),
                    positions=positions,
                    analyses=analyses,
                    evaluation=evaluation,
                    adjustments=adjustments,
                    advice=advice,
                )
                answer = self.assistant.ask(
                    str(payload.get("question") or ""),
                    context,
                    payload.get("history") if isinstance(payload.get("history"), list) else [],
                )
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
        return {
            inst_id: _rebase_forecast(
                apply_strategy_adjustments(forecast, adjustments),
                quotes.get(inst_id),
            )
            for inst_id, forecast in forecasts.items()
        }


def serve(host: str, port: int, data_dir: Path, db_path: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    storage = Storage(db_path)
    storage.init()
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
    result = {**forecast}
    result["current_price"] = live_price
    result["quote_time"] = (
        datetime.fromtimestamp(
            int(quote["ts"]) / 1000,
            tz=timezone.utc,
        ).isoformat()
        if quote.get("ts")
        else forecast.get("quote_time")
    )
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
