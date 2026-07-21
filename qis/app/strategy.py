"""Short-term strategy application separated from runtime orchestration."""

from __future__ import annotations

from qis.config import Settings
from qis.event import Event, EventEngine
from qis.models import AccountState, Candle, TradePlan
from qis.risk import RiskEngine, RiskLimits
from qis.strategy import DonchianBreakoutStrategy
from qis.trader.app import BaseApp
from qis.trader.engine import BaseEngine, MainEngine
from qis.trader.event import EVENT_SIGNAL, EVENT_TRADE_PLAN


class StrategyEngine(BaseEngine):
    """Generate risk-gated plans and publish strategy lifecycle events."""

    engine_name = "strategy"

    def __init__(
        self,
        main_engine: MainEngine,
        event_engine: EventEngine,
        settings: Settings,
    ) -> None:
        super().__init__(main_engine, event_engine)
        self.strategy = DonchianBreakoutStrategy(
            settings.donchian_lookback,
            settings.atr_period,
            settings.atr_multiplier,
            settings.ema_fast,
            settings.ema_slow,
        )
        self.risk = RiskEngine(
            RiskLimits(
                risk_per_trade=settings.risk_per_trade,
                daily_loss_limit=settings.daily_loss_limit,
                max_drawdown=settings.max_drawdown,
                max_leverage=settings.max_leverage,
                max_notional_pct=settings.max_notional_pct,
                max_trades_per_day=settings.max_trades_per_day,
            )
        )

    def evaluate(
        self,
        inst_id: str,
        candles: list[Candle],
        account: AccountState,
    ) -> TradePlan | None:
        signal = self.strategy.generate(inst_id, candles)
        if signal is None:
            return None
        self.event_engine.put(
            Event(EVENT_SIGNAL, signal, source=self.engine_name)
        )
        plan = self.risk.build_plan(signal, account)
        self.event_engine.put(
            Event(EVENT_TRADE_PLAN, plan, source=self.engine_name)
        )
        return plan


class StrategyApp(BaseApp):
    app_name = "strategy"
    display_name = "短线策略"
    engine_class = StrategyEngine
