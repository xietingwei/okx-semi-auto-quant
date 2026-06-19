from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta, timezone

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

CREATE TABLE IF NOT EXISTS spot_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inst_id TEXT NOT NULL,
    buy_time TEXT NOT NULL,
    buy_price REAL NOT NULL,
    quantity REAL NOT NULL,
    horizon TEXT NOT NULL,
    forecast_return REAL NOT NULL,
    up_probability REAL NOT NULL,
    confidence REAL NOT NULL,
    target_price REAL NOT NULL,
    notes TEXT NOT NULL,
    sell_time TEXT,
    sell_price REAL,
    realized_pnl REAL,
    realized_return REAL
);

CREATE TABLE IF NOT EXISTS spot_forecast_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inst_id TEXT NOT NULL,
    horizon TEXT NOT NULL,
    predicted_at TEXT NOT NULL,
    due_at TEXT NOT NULL,
    start_price REAL NOT NULL,
    target_price REAL NOT NULL,
    low_price REAL NOT NULL,
    high_price REAL NOT NULL,
    expected_return REAL NOT NULL,
    up_probability REAL NOT NULL,
    confidence REAL NOT NULL,
    actual_price REAL,
    actual_return REAL,
    evaluated_at TEXT,
    UNIQUE(inst_id, horizon, predicted_at)
);

CREATE TABLE IF NOT EXISTS forecast_learning_runs (
    run_at TEXT PRIMARY KEY,
    evaluated_count INTEGER NOT NULL,
    total_samples INTEGER NOT NULL,
    pending_samples INTEGER NOT NULL,
    active_horizons INTEGER NOT NULL,
    direction_accuracy REAL,
    adjustments_json TEXT NOT NULL,
    advice_json TEXT NOT NULL
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

    def open_spot_position(
        self,
        inst_id: str,
        buy_price: float,
        quantity: float,
        horizon: str,
        forecast_return: float,
        up_probability: float,
        confidence: float,
        target_price: float,
        notes: str = "",
    ) -> int:
        self.init()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO spot_positions
                (inst_id, buy_time, buy_price, quantity, horizon, forecast_return,
                 up_probability, confidence, target_price, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    inst_id,
                    utc_now().isoformat(),
                    buy_price,
                    quantity,
                    horizon,
                    forecast_return,
                    up_probability,
                    confidence,
                    target_price,
                    notes,
                ),
            )
            return int(cursor.lastrowid)

    def close_spot_position(self, position_id: int, sell_price: float) -> bool:
        self.init()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT buy_price, quantity, sell_time FROM spot_positions WHERE id = ?",
                (position_id,),
            ).fetchone()
            if row is None or row[2] is not None:
                return False
            pnl = (sell_price - float(row[0])) * float(row[1])
            realized_return = sell_price / float(row[0]) - 1
            conn.execute(
                """
                UPDATE spot_positions
                SET sell_time = ?, sell_price = ?, realized_pnl = ?, realized_return = ?
                WHERE id = ?
                """,
                (utc_now().isoformat(), sell_price, pnl, realized_return, position_id),
            )
        return True

    def spot_positions(self, limit: int = 100) -> list[sqlite3.Row]:
        self.init()
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            return list(conn.execute("SELECT * FROM spot_positions ORDER BY id DESC LIMIT ?", (limit,)))

    def spot_trade_stats(self) -> dict:
        rows = self.spot_positions(1000)
        closed = [row for row in rows if row["sell_time"] is not None]
        by_horizon: dict[str, dict] = {}
        for horizon in ("1d", "1w", "1m", "3m", "6m"):
            subset = [row for row in closed if row["horizon"] == horizon]
            if not subset:
                by_horizon[horizon] = {
                    "trades": 0,
                    "win_rate": None,
                    "direction_accuracy": None,
                    "avg_return": None,
                    "probability_gap": None,
                }
                continue
            wins = sum(1 for row in subset if float(row["realized_return"]) > 0)
            direction_hits = sum(
                1
                for row in subset
                if (float(row["forecast_return"]) > 0) == (float(row["realized_return"]) > 0)
            )
            observed_up_rate = wins / len(subset)
            avg_probability = sum(float(row["up_probability"]) for row in subset) / len(subset)
            by_horizon[horizon] = {
                "trades": len(subset),
                "win_rate": observed_up_rate,
                "direction_accuracy": direction_hits / len(subset),
                "avg_return": sum(float(row["realized_return"]) for row in subset) / len(subset),
                "probability_gap": observed_up_rate - avg_probability,
            }
        total = len(closed)
        return {
            "overall": {
                "trades": total,
                "win_rate": sum(1 for row in closed if float(row["realized_return"]) > 0) / total if total else None,
                "avg_return": sum(float(row["realized_return"]) for row in closed) / total if total else None,
            },
            "by_horizon": by_horizon,
        }

    def record_forecast_snapshot(self, forecast: dict, predicted_at: datetime | None = None) -> None:
        self.init()
        predicted_at = predicted_at or utc_now()
        with self._connect() as conn:
            for item in forecast["forecasts"]:
                due_at = predicted_at + timedelta(days=int(item["days"]))
                conn.execute(
                    """
                    INSERT OR IGNORE INTO spot_forecast_evaluations
                    (inst_id, horizon, predicted_at, due_at, start_price, target_price,
                     low_price, high_price, expected_return, up_probability, confidence)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        forecast["inst_id"],
                        item["key"],
                        predicted_at.isoformat(),
                        due_at.isoformat(),
                        float(forecast["current_price"]),
                        float(item["target"]),
                        float(item["low"]),
                        float(item["high"]),
                        float(item["expected_return"]),
                        float(item["up_probability"]),
                        float(item["confidence"]),
                    ),
                )

    def has_forecast_history(self, inst_id: str) -> bool:
        self.init()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM spot_forecast_evaluations
                WHERE inst_id = ? AND actual_price IS NOT NULL LIMIT 1
                """,
                (inst_id,),
            ).fetchone()
        return row is not None

    def record_historical_forecast_outcome(
        self,
        forecast: dict,
        predicted_at: datetime,
        actual_prices: dict[str, float],
    ) -> None:
        self.init()
        with self._connect() as conn:
            for item in forecast["forecasts"]:
                actual_price = actual_prices.get(str(item["key"]))
                if actual_price is None:
                    continue
                actual_return = actual_price / float(forecast["current_price"]) - 1
                due_at = predicted_at + timedelta(days=int(item["days"]))
                conn.execute(
                    """
                    INSERT OR IGNORE INTO spot_forecast_evaluations
                    (inst_id, horizon, predicted_at, due_at, start_price, target_price,
                     low_price, high_price, expected_return, up_probability, confidence,
                     actual_price, actual_return, evaluated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        forecast["inst_id"],
                        item["key"],
                        predicted_at.isoformat(),
                        due_at.isoformat(),
                        float(forecast["current_price"]),
                        float(item["target"]),
                        float(item["low"]),
                        float(item["high"]),
                        float(item["expected_return"]),
                        float(item["up_probability"]),
                        float(item["confidence"]),
                        actual_price,
                        actual_return,
                        due_at.isoformat(),
                    ),
                )

    def evaluate_due_forecasts(
        self,
        current_prices: dict[str, float],
        observed_at: datetime | None = None,
    ) -> int:
        self.init()
        observed_at = observed_at or utc_now()
        updated = 0
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = list(
                conn.execute(
                    """
                    SELECT * FROM spot_forecast_evaluations
                    WHERE actual_price IS NULL AND due_at <= ?
                    """,
                    (observed_at.isoformat(),),
                )
            )
            for row in rows:
                actual_price = current_prices.get(str(row["inst_id"]))
                if actual_price is None:
                    continue
                actual_return = actual_price / float(row["start_price"]) - 1
                conn.execute(
                    """
                    UPDATE spot_forecast_evaluations
                    SET actual_price = ?, actual_return = ?, evaluated_at = ?
                    WHERE id = ?
                    """,
                    (actual_price, actual_return, observed_at.isoformat(), int(row["id"])),
                )
                updated += 1
        return updated

    def forecast_evaluation(self) -> dict:
        self.init()
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            completed = list(
                conn.execute(
                    """
                    SELECT * FROM spot_forecast_evaluations
                    WHERE actual_price IS NOT NULL
                    ORDER BY id DESC LIMIT 5000
                    """
                )
            )
            pending = int(
                conn.execute(
                    "SELECT COUNT(*) FROM spot_forecast_evaluations WHERE actual_price IS NULL"
                ).fetchone()[0]
            )
        by_horizon: dict[str, dict] = {}
        for horizon in ("1d", "1w", "1m", "3m", "6m"):
            rows = [row for row in completed if row["horizon"] == horizon]
            if not rows:
                by_horizon[horizon] = {
                    "samples": 0,
                    "direction_accuracy": None,
                    "mae": None,
                    "bias": None,
                    "brier": None,
                    "interval_coverage": None,
                }
                continue
            direction_hits = sum(
                1
                for row in rows
                if (float(row["expected_return"]) >= 0) == (float(row["actual_return"]) >= 0)
            )
            errors = [
                float(row["expected_return"]) - float(row["actual_return"])
                for row in rows
            ]
            brier = sum(
                (float(row["up_probability"]) - (1 if float(row["actual_return"]) > 0 else 0)) ** 2
                for row in rows
            ) / len(rows)
            covered = sum(
                1
                for row in rows
                if float(row["low_price"]) <= float(row["actual_price"]) <= float(row["high_price"])
            )
            by_horizon[horizon] = {
                "samples": len(rows),
                "direction_accuracy": direction_hits / len(rows),
                "mae": sum(abs(error) for error in errors) / len(rows),
                "bias": sum(errors) / len(rows),
                "brier": brier,
                "interval_coverage": covered / len(rows),
            }
        total = len(completed)
        return {
            "overall": {
                "samples": total,
                "pending": pending,
                "direction_accuracy": (
                    sum(
                        1
                        for row in completed
                        if (float(row["expected_return"]) >= 0)
                        == (float(row["actual_return"]) >= 0)
                    )
                    / total
                    if total
                    else None
                ),
            },
            "by_horizon": by_horizon,
        }

    def forecast_advice(self) -> list[dict[str, str]]:
        evaluation = self.forecast_evaluation()
        advice: list[dict[str, str]] = []
        labels = {"1d": "1天", "1w": "1周", "1m": "1月", "3m": "3月", "6m": "6月"}
        for horizon, stats in evaluation["by_horizon"].items():
            samples = int(stats["samples"])
            if samples < 10:
                advice.append(
                    {
                        "level": "sample",
                        "title": f"{labels[horizon]}历史预测待积累",
                        "detail": f"已有 {samples} 个到期预测样本；至少 10 个后再判断该周期是否需要调整。",
                    }
                )
                continue
            accuracy = float(stats["direction_accuracy"])
            mae = float(stats["mae"])
            bias = float(stats["bias"])
            brier = float(stats["brier"])
            coverage = float(stats["interval_coverage"])
            if accuracy < 0.5:
                advice.append(
                    {
                        "level": "risk",
                        "title": f"重训{labels[horizon]}方向模型",
                        "detail": f"历史预测与真实走势的方向命中率仅 {accuracy * 100:.1f}%，应降低动量权重并增加行情状态分层。",
                    }
                )
            if mae > 0.08:
                advice.append(
                    {
                        "level": "error",
                        "title": f"收缩{labels[horizon]}收益幅度",
                        "detail": f"预测收益与真实收益的平均绝对误差为 {mae * 100:.1f}%，目标价振幅偏大。",
                    }
                )
            if abs(bias) > 0.03:
                advice.append(
                    {
                        "level": "bias",
                        "title": f"修正{labels[horizon]}系统性偏差",
                        "detail": f"预测收益相对真实收益平均{'高估' if bias > 0 else '低估'} {abs(bias) * 100:.1f} 个百分点。",
                    }
                )
            if brier > 0.24:
                advice.append(
                    {
                        "level": "calibration",
                        "title": f"重新校准{labels[horizon]}上涨概率",
                        "detail": f"Brier 分数为 {brier:.3f}，概率置信度与真实涨跌不匹配，建议采用时间序列样本外校准。",
                    }
                )
            if coverage < 0.65:
                advice.append(
                    {
                        "level": "interval",
                        "title": f"扩大{labels[horizon]}预测区间",
                        "detail": f"真实价格落入预测区间的比例仅 {coverage * 100:.1f}%，尾部波动覆盖不足。",
                    }
                )
        return advice or [{
            "level": "good",
            "title": "历史预测表现稳定",
            "detail": "当前到期预测的方向、收益误差、概率与区间覆盖未发现明显异常。",
        }]

    def forecast_strategy_adjustments(self, minimum_samples: int = 30) -> dict[str, dict]:
        self.init()
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            completed = list(
                conn.execute(
                    """
                    SELECT * FROM spot_forecast_evaluations
                    WHERE actual_price IS NOT NULL
                    ORDER BY evaluated_at DESC LIMIT 5000
                    """
                )
            )
        result: dict[str, dict] = {}
        for horizon in ("1d", "1w", "1m", "3m", "6m"):
            rows = [row for row in completed if row["horizon"] == horizon][:1200]
            samples = len(rows)
            if samples < minimum_samples:
                result[horizon] = {"active": False, "samples": samples}
                continue

            predicted = [float(row["expected_return"]) for row in rows]
            actual = [float(row["actual_return"]) for row in rows]
            probabilities = [float(row["up_probability"]) for row in rows]
            # Prefer recent outcomes while retaining a long enough effective history.
            weights = [0.997 ** index for index in range(samples)]
            weight_sum = sum(weights)
            weighted = lambda values: sum(
                weight * value for weight, value in zip(weights, values)
            ) / weight_sum
            actual_up = [1.0 if value > 0 else 0.0 for value in actual]
            actual_up_rate = weighted(actual_up)
            direction_accuracy = weighted([
                1.0 if (left >= 0) == (right >= 0) else 0.0
                for left, right in zip(predicted, actual)
            ])
            coverage = weighted([
                1.0
                if float(row["low_price"])
                <= float(row["actual_price"])
                <= float(row["high_price"])
                else 0.0
                for row in rows
            ])

            # Bounded weighted regression: actual_return ~= shift + scale * prediction.
            predicted_mean = weighted(predicted)
            actual_mean = weighted(actual)
            covariance = weighted([
                (left - predicted_mean) * (right - actual_mean)
                for left, right in zip(predicted, actual)
            ])
            predicted_variance = weighted([
                (value - predicted_mean) ** 2 for value in predicted
            ])
            raw_return_scale = covariance / max(predicted_variance, 1e-6)
            direction_reliability = _clip(
                (direction_accuracy - 0.44) / 0.18,
                0.0,
                1.0,
            )
            return_scale = _clip(raw_return_scale, 0.12, 1.20)
            return_scale *= 0.35 + 0.65 * direction_reliability
            return_shift = actual_mean - return_scale * predicted_mean

            # Calibrate probability slope, not just its mean. Poor directional
            # histories are deliberately pulled toward 50% instead of inverted.
            centered_probabilities = [value - 0.5 for value in probabilities]
            centered_outcomes = [value - 0.5 for value in actual_up]
            probability_covariance = weighted([
                left * right
                for left, right in zip(
                    centered_probabilities,
                    centered_outcomes,
                )
            ])
            probability_variance = weighted([
                value ** 2 for value in centered_probabilities
            ])
            probability_scale = _clip(
                probability_covariance / max(probability_variance, 1e-6),
                0.15,
                1.05,
            )
            probability_scale *= 0.45 + 0.55 * direction_reliability
            average_probability = weighted(probabilities)
            probability_shift = actual_up_rate - (
                0.5 + (average_probability - 0.5) * probability_scale
            )
            interval_scale = _clip(0.80 / max(coverage, 0.30), 0.80, 1.50)
            result[horizon] = {
                "active": True,
                "samples": samples,
                "return_shift": _clip(return_shift, -0.08, 0.08),
                "return_scale": _clip(return_scale, 0.10, 1.20),
                "probability_shift": _clip(probability_shift, -0.15, 0.15),
                "probability_scale": _clip(probability_scale, 0.10, 1.05),
                "interval_scale": interval_scale,
                "direction_accuracy": direction_accuracy,
                "coverage": coverage,
                "calibration_method": "recency_weighted_bounded_regression_v2",
            }
        return result

    def record_forecast_learning_run(
        self,
        run_at: datetime,
        evaluated_count: int,
        evaluation: dict,
        adjustments: dict,
        advice: list[dict],
    ) -> None:
        self.init()
        overall = evaluation.get("overall", {})
        active_horizons = sum(
            1 for item in adjustments.values() if item.get("active")
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO forecast_learning_runs
                (run_at, evaluated_count, total_samples, pending_samples,
                 active_horizons, direction_accuracy, adjustments_json, advice_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_at.isoformat(),
                    int(evaluated_count),
                    int(overall.get("samples") or 0),
                    int(overall.get("pending") or 0),
                    active_horizons,
                    overall.get("direction_accuracy"),
                    json.dumps(adjustments, ensure_ascii=False),
                    json.dumps(advice, ensure_ascii=False),
                ),
            )

    def latest_forecast_learning_run(self) -> dict | None:
        self.init()
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT * FROM forecast_learning_runs
                ORDER BY run_at DESC LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        return {
            "run_at": row["run_at"],
            "evaluated_count": int(row["evaluated_count"]),
            "total_samples": int(row["total_samples"]),
            "pending_samples": int(row["pending_samples"]),
            "active_horizons": int(row["active_horizons"]),
            "direction_accuracy": row["direction_accuracy"],
            "adjustments": json.loads(row["adjustments_json"]),
            "advice": json.loads(row["advice_json"]),
        }

    # Backwards-compatible alias for callers that still display trade-result statistics.
    def spot_reliability(self) -> dict:
        return self.spot_trade_stats()

    def spot_advice(self) -> list[dict[str, str]]:
        return self.forecast_advice()

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


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
