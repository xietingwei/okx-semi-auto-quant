from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import Event as ThreadEvent

from qis.app.strategy import StrategyApp, StrategyEngine
from qis.config import load_settings
from qis.event import Event, EventEngine
from qis.models import AccountState, Candle
from qis.runtime import create_runtime
from qis.trader.event import EVENT_BAR, EVENT_TRADE_PLAN
from qis.trader.object import BarBatchData, HistoryRequest


class StubOkxClient:
    simulated = True

    def __init__(self, candles: list[Candle]) -> None:
        self.candles = candles
        self.requests: list[tuple[str, str, int]] = []

    def public_range_candles(
        self,
        inst_id: str,
        bar: str,
        limit: int,
    ) -> list[Candle]:
        self.requests.append((inst_id, bar, limit))
        return self.candles[-limit:]


def _candles(count: int = 8) -> list[Candle]:
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    rows = []
    for index in range(count):
        close = 100.0 + index
        rows.append(
            Candle(
                ts=start + timedelta(days=index),
                open=close - 0.5,
                high=close + 1,
                low=close - 1,
                close=close,
                volume=1_000 + index,
            )
        )
    return rows


def test_event_engine_isolates_handler_errors_and_keeps_dispatching() -> None:
    errors = []
    received = []
    completed = ThreadEvent()
    engine = EventEngine(
        error_handler=lambda exc, event, handler: errors.append((str(exc), event.type))
    )

    def broken(event: Event) -> None:
        raise RuntimeError("broken app")

    def healthy(event: Event) -> None:
        received.append((event.type, event.data))
        completed.set()

    engine.register("market", broken)
    engine.register("market", healthy)
    engine.start(timer=False)
    engine.put(Event("market", {"price": 100}))

    assert completed.wait(1)
    engine.stop()
    assert errors == [("broken app", "market")]
    assert received == [("market", {"price": 100})]
    assert engine.active is False


def test_runtime_routes_history_through_gateway_and_market_app(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)
    settings = replace(load_settings(), db_path=tmp_path / "qis.sqlite3")
    source = StubOkxClient(_candles())
    bar_event = ThreadEvent()
    events: list[BarBatchData] = []

    with create_runtime(settings, okx_client=source) as runtime:
        runtime.event_engine.register(
            EVENT_BAR,
            lambda event: (events.append(event.data), bar_event.set()),
        )
        rows = runtime.market_data.query_history(
            HistoryRequest("BTC-USDT", "1D", 5)
        )

        assert bar_event.wait(1)
        assert rows == source.candles[-5:]
        assert source.requests == [("BTC-USDT", "1D", 5)]
        assert runtime.market_data.cached_history("BTC-USDT", "1D") == tuple(rows)
        assert events[0].gateway_name == "OKX"
        assert events[0].inst_id == "BTC-USDT"

    assert runtime.event_engine.active is False
    assert runtime.okx.connected is False


def test_strategy_app_publishes_a_risk_gated_trade_plan(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    settings = replace(
        load_settings(),
        db_path=tmp_path / "qis.sqlite3",
        donchian_lookback=3,
        atr_period=2,
        ema_fast=0,
        ema_slow=0,
    )
    rows = _candles()
    # The strategy ignores the last, still-forming candle. Make the prior one
    # a clean breakout over the three-bar Donchian window.
    rows[-2] = Candle(
        rows[-2].ts,
        open=106,
        high=112,
        low=105,
        close=111,
        volume=2_000,
    )
    source = StubOkxClient(rows)
    plan_event = ThreadEvent()
    plans = []

    with create_runtime(settings, okx_client=source) as runtime:
        installed = runtime.main_engine.add_app(StrategyApp, settings)
        assert isinstance(installed, StrategyEngine)
        runtime.event_engine.register(
            EVENT_TRADE_PLAN,
            lambda event: (plans.append(event.data), plan_event.set()),
        )
        candles = runtime.market_data.query_history(
            HistoryRequest("BTC-USDT", "1D", len(rows))
        )
        plan = installed.evaluate(
            "BTC-USDT",
            candles,
            AccountState(5_000, 5_000, 0, 0, 0),
        )

        assert plan is not None
        assert plan.approved is True
        assert plan_event.wait(1)
        assert plans == [plan]
