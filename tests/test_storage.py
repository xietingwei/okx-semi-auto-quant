from pathlib import Path

from qis.models import Side, Signal, TradePlan, utc_now
from qis.storage import Storage


def test_storage_counts_approved_trades_today(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "qis.sqlite3")
    signal = Signal("BTC-USDT-SWAP", Side.BUY, 100.0, 95.0, 110.0, "test", 0.5, utc_now())
    storage.save_plan(TradePlan(signal, 1.0, 100.0, 5.0, 0.02, True, "approved"))
    storage.save_plan(TradePlan(signal, 0.0, 0.0, 0.0, 0.0, False, "rejected"))

    assert storage.approved_trades_today() == 1


def test_storage_records_manual_trade_stats(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "qis.sqlite3")
    storage.record_manual_trade(
        inst_id="ETH-USDT-SWAP",
        side="buy",
        entry=100.0,
        exit_price=110.0,
        size=1.0,
        stop=95.0,
        take_profit=115.0,
        model="walkforward_calibrated_macro_intel_v4",
        estimated_probability=0.7,
        notes="test",
    )

    stats = storage.manual_trade_stats()

    assert stats["trades"] == 1
    assert stats["win_rate"] == 1.0
    assert stats["avg_r"] == 2.0
