from qis.models import AccountState, Side, Signal, utc_now
from qis.risk import RiskEngine, RiskLimits
from qis.strategy import DonchianBreakoutStrategy


def test_risk_engine_caps_notional() -> None:
    engine = RiskEngine(RiskLimits(0.01, 0.03, 0.12, 2, 0.35, 6))
    signal = Signal("BTC-USDT-SWAP", Side.BUY, 100.0, 90.0, 120.0, "test", 0.5, utc_now())
    account = AccountState(5000.0, 5000.0, 0.0, 0.0, 0)

    plan = engine.build_plan(signal, account)

    assert plan.approved
    assert plan.notional <= 1750.0
    assert plan.risk_amount <= 50.0


def test_risk_engine_rejects_daily_loss_limit() -> None:
    engine = RiskEngine(RiskLimits(0.01, 0.03, 0.12, 2, 0.35, 6))
    signal = Signal("BTC-USDT-SWAP", Side.SELL, 100.0, 110.0, 80.0, "test", 0.5, utc_now())
    account = AccountState(5000.0, 5000.0, -200.0, 0.0, 0)

    plan = engine.build_plan(signal, account)

    assert not plan.approved
    assert plan.reason == "daily loss limit reached"


def test_ema_tracks_latest_values() -> None:
    ema = DonchianBreakoutStrategy._ema([1, 1, 1, 2, 3], 3)

    assert ema > 1
    assert ema < 3
