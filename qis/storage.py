from __future__ import annotations

import sqlite3
from pathlib import Path
from datetime import timezone

from qis.models import AccountState, TradePlan, utc_now


SCHEMA = """
CREATE TABLE IF NOT EXISTS account_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    equity REAL NOT NULL,
    peak_equity REAL NOT NULL,
    daily_pnl REAL NOT NULL,
    open_notional REAL NOT NULL,
    trades_today INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS trade_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    inst_id TEXT NOT NULL,
    side TEXT NOT NULL,
    entry REAL NOT NULL,
    stop REAL NOT NULL,
    take_profit REAL,
    size REAL NOT NULL,
    notional REAL NOT NULL,
    risk_amount REAL NOT NULL,
    leverage REAL NOT NULL,
    approved INTEGER NOT NULL,
    reason TEXT NOT NULL,
    signal_reason TEXT NOT NULL,
    confidence REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS manual_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opened_at TEXT NOT NULL,
    closed_at TEXT NOT NULL,
    inst_id TEXT NOT NULL,
    side TEXT NOT NULL,
    entry REAL NOT NULL,
    exit REAL NOT NULL,
    stop REAL,
    take_profit REAL,
    size REAL NOT NULL,
    pnl REAL NOT NULL,
    r_multiple REAL NOT NULL,
    model TEXT NOT NULL,
    estimated_probability REAL,
    notes TEXT NOT NULL
);
"""


class Storage:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def init(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def save_account(self, account: AccountState) -> None:
        self.init()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO account_snapshots
                (created_at, equity, peak_equity, daily_pnl, open_notional, trades_today)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_now().isoformat(),
                    account.equity,
                    account.peak_equity,
                    account.daily_pnl,
                    account.open_notional,
                    account.trades_today,
                ),
            )

    def save_plan(self, plan: TradePlan) -> None:
        self.init()
        signal = plan.signal
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO trade_plans
                (created_at, inst_id, side, entry, stop, take_profit, size, notional,
                 risk_amount, leverage, approved, reason, signal_reason, confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal.created_at.isoformat(),
                    signal.inst_id,
                    signal.side.value,
                    signal.entry,
                    signal.stop,
                    signal.take_profit,
                    plan.size,
                    plan.notional,
                    plan.risk_amount,
                    plan.leverage,
                    1 if plan.approved else 0,
                    plan.reason,
                    signal.reason,
                    signal.confidence,
                ),
            )

    def latest_plans(self, limit: int = 10) -> list[sqlite3.Row]:
        self.init()
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            return list(
                conn.execute(
                    "SELECT * FROM trade_plans ORDER BY id DESC LIMIT ?",
                    (limit,),
                )
            )

    def record_manual_trade(
        self,
        inst_id: str,
        side: str,
        entry: float,
        exit_price: float,
        size: float,
        stop: float | None,
        take_profit: float | None,
        model: str,
        estimated_probability: float | None,
        notes: str = "",
    ) -> int:
        self.init()
        direction = 1 if side == "buy" else -1
        pnl = (exit_price - entry) * size * direction
        risk_per_unit = abs(entry - stop) if stop is not None else abs(entry - exit_price)
        r_multiple = pnl / (risk_per_unit * size) if risk_per_unit > 0 and size > 0 else 0.0
        now = utc_now().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO manual_trades
                (opened_at, closed_at, inst_id, side, entry, exit, stop, take_profit, size,
                 pnl, r_multiple, model, estimated_probability, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    now,
                    inst_id,
                    side,
                    entry,
                    exit_price,
                    stop,
                    take_profit,
                    size,
                    pnl,
                    r_multiple,
                    model,
                    estimated_probability,
                    notes,
                ),
            )
            return int(cursor.lastrowid)

    def manual_trade_stats(self, model: str | None = None, limit: int | None = None) -> dict[str, float | int | None]:
        self.init()
        where = "WHERE model = ?" if model else ""
        params: tuple[object, ...] = (model,) if model else ()
        suffix = f" ORDER BY id DESC LIMIT {int(limit)}" if limit else ""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = list(conn.execute(f"SELECT * FROM manual_trades {where}{suffix}", params))
        if not rows:
            return {
                "trades": 0,
                "win_rate": None,
                "avg_r": None,
                "profit_factor": None,
                "avg_estimated_probability": None,
                "calibration_error": None,
            }
        wins = sum(1 for row in rows if row["pnl"] > 0)
        gross_profit = sum(float(row["pnl"]) for row in rows if row["pnl"] > 0)
        gross_loss = abs(sum(float(row["pnl"]) for row in rows if row["pnl"] < 0))
        estimates = [float(row["estimated_probability"]) for row in rows if row["estimated_probability"] is not None]
        win_rate = wins / len(rows)
        avg_estimate = sum(estimates) / len(estimates) if estimates else None
        return {
            "trades": len(rows),
            "win_rate": win_rate,
            "avg_r": sum(float(row["r_multiple"]) for row in rows) / len(rows),
            "profit_factor": gross_profit / gross_loss if gross_loss else (gross_profit if gross_profit else 0.0),
            "avg_estimated_probability": avg_estimate,
            "calibration_error": win_rate - avg_estimate if avg_estimate is not None else None,
        }

    def latest_manual_trades(self, limit: int = 10) -> list[sqlite3.Row]:
        self.init()
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            return list(conn.execute("SELECT * FROM manual_trades ORDER BY id DESC LIMIT ?", (limit,)))

    def approved_trades_today(self) -> int:
        self.init()
        today = utc_now().astimezone(timezone.utc).date().isoformat()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) FROM trade_plans
                WHERE approved = 1 AND substr(created_at, 1, 10) = ?
                """,
                (today,),
            ).fetchone()
        return int(row[0])

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)
