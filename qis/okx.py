from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any

from qis.models import Candle, Side


class OkxError(RuntimeError):
    pass


class OkxClient:
    BASE_URL = "https://www.okx.com"

    def __init__(self, api_key: str = "", api_secret: str = "", passphrase: str = "", simulated: bool = True) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.simulated = simulated

    def public_candles(self, inst_id: str, bar: str = "15m", limit: int = 100) -> list[Candle]:
        data = self._request(
            "GET",
            "/api/v5/market/candles",
            params={"instId": inst_id, "bar": bar, "limit": str(limit)},
            auth=False,
        )
        candles = []
        for row in data:
            candles.append(
                Candle(
                    ts=datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                )
            )
        return sorted(candles, key=lambda item: item.ts)

    def balance_equity(self, ccy: str = "USDT") -> float | None:
        data = self._request("GET", "/api/v5/account/balance", auth=True)
        for detail in data[0].get("details", []):
            if detail.get("ccy") == ccy:
                value = detail.get("eq") or detail.get("cashBal")
                return float(value)
        return None

    def public_instrument(self, inst_id: str) -> dict[str, Any]:
        inst_type = self._infer_inst_type(inst_id)
        data = self._request(
            "GET",
            "/api/v5/public/instruments",
            params={"instType": inst_type, "instId": inst_id},
            auth=False,
        )
        if not data:
            raise OkxError(f"instrument not found: {inst_id}")
        return data[0]

    def public_instruments(self, inst_type: str) -> list[dict[str, Any]]:
        return self._request(
            "GET",
            "/api/v5/public/instruments",
            params={"instType": inst_type},
            auth=False,
        )

    def public_tickers(self, inst_type: str) -> list[dict[str, Any]]:
        return self._request(
            "GET",
            "/api/v5/market/tickers",
            params={"instType": inst_type},
            auth=False,
        )

    def public_order_book(self, inst_id: str, depth: int = 20) -> dict[str, Any]:
        rows = self._request(
            "GET",
            "/api/v5/market/books",
            params={"instId": inst_id, "sz": str(max(1, min(depth, 400)))},
            auth=False,
            attempts=1,
            timeout=8,
        )
        return rows[0] if rows else {}

    def public_open_interest(self, inst_type: str = "SWAP") -> list[dict[str, Any]]:
        return self._request(
            "GET",
            "/api/v5/public/open-interest",
            params={"instType": inst_type},
            auth=False,
            attempts=1,
            timeout=8,
        )

    def public_funding_rate(self, inst_id: str) -> dict[str, Any]:
        rows = self._request(
            "GET",
            "/api/v5/public/funding-rate",
            params={"instId": inst_id},
            auth=False,
            attempts=1,
            timeout=8,
        )
        return rows[0] if rows else {}

    def order_size_from_base(self, inst_id: str, base_size: float) -> str:
        instrument = self.public_instrument(inst_id)
        return self.contract_size_from_base(
            base_size,
            instrument.get("ctVal") or "1",
            instrument.get("lotSz") or "1",
            instrument.get("minSz") or instrument.get("lotSz") or "1",
        )

    @staticmethod
    def contract_size_from_base(base_size: float, ct_val: str, lot_sz: str, min_sz: str) -> str:
        contract_value = Decimal(ct_val)
        lot = Decimal(lot_sz)
        minimum = Decimal(min_sz)
        raw = Decimal(str(base_size)) / contract_value
        lots = (raw / lot).to_integral_value(rounding=ROUND_DOWN) * lot
        if lots < minimum:
            raise OkxError(f"order size below minSz after conversion: {lots} < {minimum}")
        return format(lots.normalize(), "f")

    def place_market_order(self, inst_id: str, side: Side, size: str | float, td_mode: str = "cross") -> dict[str, Any]:
        size_text = size if isinstance(size, str) else f"{size:.8f}".rstrip("0").rstrip(".")
        body = {
            "instId": inst_id,
            "tdMode": td_mode,
            "side": side.value,
            "ordType": "market",
            "sz": size_text,
        }
        return self._request("POST", "/api/v5/trade/order", body=body, auth=True)

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        auth: bool = False,
        attempts: int = 3,
        timeout: float = 15,
    ) -> Any:
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        url = f"{self.BASE_URL}{path}{query}"
        payload = json.dumps(body or {}, separators=(",", ":")) if body else ""
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "okx-semi-auto-quant/0.1",
        }
        if self.simulated and auth:
            headers["x-simulated-trading"] = "1"
        if auth:
            headers.update(self._auth_headers(method, path + query, payload))
        request = urllib.request.Request(
            url,
            data=payload.encode() if payload else None,
            headers=headers,
            method=method,
        )
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    raw = response.read().decode()
                break
            except Exception as exc:
                last_error = exc
                if attempt < attempts - 1:
                    time.sleep(0.5 * (attempt + 1))
        else:
            raise OkxError(f"OKX request failed: {last_error}") from last_error
        decoded = json.loads(raw)
        if decoded.get("code") != "0":
            raise OkxError(f"OKX error {decoded.get('code')}: {decoded.get('msg')}")
        return decoded.get("data", [])

    def _auth_headers(self, method: str, request_path: str, body: str) -> dict[str, str]:
        if not self.api_key or not self.api_secret or not self.passphrase:
            raise OkxError("OKX credentials are required for private endpoints")
        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        message = f"{timestamp}{method.upper()}{request_path}{body}"
        digest = hmac.new(self.api_secret.encode(), message.encode(), hashlib.sha256).digest()
        sign = base64.b64encode(digest).decode()
        return {
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": sign,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
        }

    @staticmethod
    def _infer_inst_type(inst_id: str) -> str:
        if inst_id.endswith("-SWAP"):
            return "SWAP"
        if "-" in inst_id:
            return "SPOT"
        return "SWAP"
