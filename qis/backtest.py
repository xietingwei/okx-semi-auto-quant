from __future__ import annotations

from dataclasses import dataclass

from qis.models import Candle, Side
from qis.risk import RiskEngine
from qis.strategy import DonchianBreakoutStrategy
from qis.models import AccountState


@dataclass(frozen=True)
class BacktestTrade:
    entry_ts: str
    exit_ts: str
    side: str
    entry: float
    exit: float
    stop: float
    take_profit: float | None
    size: float
    pnl: float
    reason: str


@dataclass(frozen=True)
class BacktestResult:
    starting_equity: float
    ending_equity: float
    peak_equity: float
    max_drawdown: float
    trades: list[BacktestTrade]

    @property
    def total_return(self) -> float:
        return (self.ending_equity / self.starting_equity - 1) if self.starting_equity else 0.0

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        wins = sum(1 for item in self.trades if item.pnl > 0)
        return wins / len(self.trades)

    @property
    def profit_factor(self) -> float:
        gross_profit = sum(item.pnl for item in self.trades if item.pnl > 0)
        gross_loss = abs(sum(item.pnl for item in self.trades if item.pnl < 0))
        if gross_loss == 0:
            return gross_profit if gross_profit else 0.0
        return gross_profit / gross_loss


class Backtester:
    def __init__(
        self,
        strategy: DonchianBreakoutStrategy,
        risk: RiskEngine,
        starting_equity: float,
        max_hold_bars: int = 48,
        fee_rate: float = 0.0005,
    ) -> None:
        self.strategy = strategy
        self.risk = risk
        self.starting_equity = starting_equity
        self.max_hold_bars = max_hold_bars
        self.fee_rate = fee_rate

    def run(self, inst_id: str, candles: list[Candle]) -> BacktestResult:
        equity = self.starting_equity
        peak = self.starting_equity
        max_drawdown = 0.0
        trades: list[BacktestTrade] = []
        i = 40
        while i < len(candles) - 2:
            account = AccountState(equity=equity, peak_equity=peak, daily_pnl=0.0, open_notional=0.0, trades_today=0)
            signal = self.strategy.generate(inst_id, candles[: i + 1])
            if signal is None:
                i += 1
                continue
            plan = self.risk.build_plan(signal, account)
            if not plan.approved:
                i += 1
                continue
            exit_candle, exit_price = self._find_exit(signal.side, signal.stop, signal.take_profit, candles[i + 1 : i + 1 + self.max_hold_bars])
            gross = (exit_price - signal.entry) * plan.size
            if signal.side is Side.SELL:
                gross = -gross
            fees = (signal.entry * plan.size + exit_price * plan.size) * self.fee_rate
            pnl = gross - fees
            equity += pnl
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, 1 - equity / peak if peak else 0.0)
            trades.append(
                BacktestTrade(
                    entry_ts=candles[i].ts.isoformat(),
                    exit_ts=exit_candle.ts.isoformat(),
                    side=signal.side.value,
                    entry=signal.entry,
                    exit=exit_price,
                    stop=signal.stop,
                    take_profit=signal.take_profit,
                    size=plan.size,
                    pnl=pnl,
                    reason=signal.reason,
                )
            )
            i += self.max_hold_bars
        return BacktestResult(self.starting_equity, equity, peak, max_drawdown, trades)

    @staticmethod
    def _find_exit(side: Side, stop: float, take_profit: float | None, future: list[Candle]) -> tuple[Candle, float]:
        for candle in future:
            if side is Side.BUY:
                if candle.low <= stop:
                    return candle, stop
                if take_profit is not None and candle.high >= take_profit:
                    return candle, take_profit
            else:
                if candle.high >= stop:
                    return candle, stop
                if take_profit is not None and candle.low <= take_profit:
                    return candle, take_profit
        last = future[-1]
        return last, last.close
