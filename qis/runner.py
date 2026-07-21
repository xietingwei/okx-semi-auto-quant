from __future__ import annotations

import time

from qis.app.strategy import StrategyApp, StrategyEngine
from qis.config import Settings
from qis.event import Event
from qis.models import AccountState, Mode, TradePlan
from qis.okx import OkxError
from qis.runtime import QisRuntime, create_runtime
from qis.storage import Storage
from qis.trader.event import EVENT_ACCOUNT
from qis.trader.object import HistoryRequest


class Runner:
    def __init__(
        self,
        settings: Settings,
        runtime: QisRuntime | None = None,
    ) -> None:
        self.settings = settings
        self.runtime = runtime or create_runtime(settings)
        self.client = self.runtime.okx
        self.storage = Storage(settings.db_path)
        strategy_engine = self.runtime.main_engine.add_app(
            StrategyApp,
            settings,
        )
        if not isinstance(strategy_engine, StrategyEngine):
            self.runtime.close()
            raise TypeError("StrategyApp installed an invalid engine")
        self.strategy_engine = strategy_engine
        # Compatibility attributes for callers that inspect the current
        # strategy/risk configuration.
        self.strategy = strategy_engine.strategy
        self.risk = strategy_engine.risk

    def close(self) -> None:
        self.runtime.close()

    def __enter__(self) -> "Runner":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def run(self, once: bool = False) -> None:
        self.storage.init()
        try:
            while True:
                try:
                    self.tick()
                except Exception as exc:
                    print(f"Tick failed, retrying on next interval: {exc}", flush=True)
                if once:
                    return
                time.sleep(self.settings.loop_seconds)
        finally:
            self.close()

    def tick(self) -> TradePlan | None:
        if self.settings.pause_file.exists():
            print(f"Paused by {self.settings.pause_file}. Remove it or run resume before trading.")
            return None
        account = self._account_state()
        self.storage.save_account(account)
        candles = self.runtime.market_data.query_history(
            HistoryRequest(self.settings.inst_id, self.settings.bar, 120)
        )
        plan = self.strategy_engine.evaluate(
            self.settings.inst_id,
            candles,
            account,
        )
        if plan is None:
            print(f"No signal for {self.settings.inst_id} on {self.settings.bar}.")
            return None
        self.storage.save_plan(plan)
        self._print_plan(plan)
        if plan.approved and self.settings.mode is Mode.LIVE:
            self._confirm_and_execute(plan)
        return plan

    def scan(self) -> list[TradePlan]:
        if self.settings.pause_file.exists():
            print(f"Paused by {self.settings.pause_file}. Remove it or run resume before trading.")
            return []
        account = self._account_state()
        self.storage.save_account(account)
        plans: list[TradePlan] = []
        for inst_id in tuple(dict.fromkeys(self.settings.inst_ids + self.settings.stock_inst_ids)):
            candles = self.runtime.market_data.query_history(
                HistoryRequest(inst_id, self.settings.bar, 120)
            )
            plan = self.strategy_engine.evaluate(inst_id, candles, account)
            if plan is None:
                print(f"No signal for {inst_id} on {self.settings.bar}.")
                continue
            self.storage.save_plan(plan)
            self._print_plan(plan)
            plans.append(plan)
        return plans

    def _account_state(self) -> AccountState:
        equity = None
        if self.settings.mode is Mode.LIVE or self._has_credentials():
            try:
                equity = self.client.balance_equity()
            except OkxError as exc:
                if self.settings.mode is Mode.LIVE:
                    raise
                print(f"Private account unavailable, using configured paper equity: {exc}")
        equity = equity or self.settings.initial_equity
        account = AccountState(
            equity=equity,
            peak_equity=max(equity, self.settings.initial_equity),
            daily_pnl=0.0,
            open_notional=0.0,
            trades_today=self.storage.approved_trades_today(),
        )
        self.runtime.event_engine.put(
            Event(EVENT_ACCOUNT, account, source="Runner")
        )
        return account

    def _has_credentials(self) -> bool:
        return bool(self.settings.okx_api_key and self.settings.okx_api_secret and self.settings.okx_api_passphrase)

    def _confirm_and_execute(self, plan: TradePlan) -> None:
        order_size = self.client.order_size_from_base(plan.signal.inst_id, plan.size)
        prompt = (
            f"Type EXECUTE to place {plan.signal.side.value} market order "
            f"{plan.size:.6f} base units / OKX sz={order_size} {plan.signal.inst_id}: "
        )
        if input(prompt).strip() != "EXECUTE":
            print("Order skipped.")
            return
        result = self.client.place_market_order(plan.signal.inst_id, plan.signal.side, order_size)
        print(f"Order submitted: {result}")

    @staticmethod
    def _print_plan(plan: TradePlan) -> None:
        signal = plan.signal
        status = "APPROVED" if plan.approved else "REJECTED"
        print(f"{status} {signal.inst_id} {signal.side.value.upper()}")
        print(f"entry={signal.entry:.4f} stop={signal.stop:.4f} take_profit={signal.take_profit}")
        print(f"size={plan.size:.8f} notional={plan.notional:.2f} risk={plan.risk_amount:.2f} lev={plan.leverage:.2f}")
        print(f"signal={signal.reason}")
        print(f"risk={plan.reason}")
