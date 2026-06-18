from __future__ import annotations

import argparse
from pathlib import Path

from qis.analysis_report import render_analysis_report
from qis.analyzer import MarketAnalyzer, summarize_opportunities
from qis.backtest import Backtester
from qis.config import load_settings
from qis.dashboard import render_dashboard
from qis.doctor import run_doctor
from qis.external_intel import ExternalIntelAnalyzer
from qis.macro import MacroAnalyzer
from qis.models import Mode
from qis.okx import OkxClient, OkxError
from qis.portal import render_portal
from qis.risk import RiskEngine, RiskLimits
from qis.runner import Runner
from qis.storage import Storage
from qis.strategy import DonchianBreakoutStrategy


def main() -> None:
    parser = argparse.ArgumentParser(prog="qis")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="run strategy loop")
    run_parser.add_argument("--once", action="store_true", help="run one tick and exit")
    run_parser.add_argument("--paper", action="store_true", help="force paper mode")
    run_parser.add_argument("--live", action="store_true", help="force live mode")
    scan_parser = sub.add_parser("scan", help="scan configured instruments once")
    scan_parser.add_argument("--paper", action="store_true", help="force paper mode")

    sub.add_parser("init-db", help="initialize sqlite database")
    status_parser = sub.add_parser("status", help="show recent trade plans")
    status_parser.add_argument("--limit", type=int, default=10)
    dash_parser = sub.add_parser("dashboard", help="render local HTML dashboard")
    dash_parser.add_argument("--out", default="data/dashboard.html")
    portal_parser = sub.add_parser("portal", help="render unified local web portal")
    portal_parser.add_argument("--out", default="data/index.html")
    backtest_parser = sub.add_parser("backtest", help="backtest current strategy on OKX candles")
    backtest_parser.add_argument("--limit", type=int, default=300)
    backtest_parser.add_argument("--max-hold-bars", type=int, default=48)
    backtest_parser.add_argument("--fee-rate", type=float, default=0.0005)
    analyze_parser = sub.add_parser("analyze", help="rank market opportunities with estimated success rates")
    analyze_parser.add_argument("--limit", type=int, default=300)
    analyze_parser.add_argument("--top", type=int, default=10)
    analyze_parser.add_argument("--html", default="data/analysis.html")
    analyze_parser.add_argument("--min-success", type=float, default=None)
    analyze_parser.add_argument("--show-all", action="store_true")
    analyze_parser.add_argument("--no-macro", action="store_true")
    analyze_parser.add_argument("--no-intel", action="store_true")
    sub.add_parser("doctor", help="run pre-trade system checks")
    sub.add_parser("pause", help="create pause file and stop trading loop")
    sub.add_parser("resume", help="remove pause file")
    trade_add = sub.add_parser("trade-add", help="record a manually executed trade result")
    trade_add.add_argument("--inst", required=True)
    trade_add.add_argument("--side", choices=["buy", "sell"], required=True)
    trade_add.add_argument("--entry", type=float, required=True)
    trade_add.add_argument("--exit", type=float, required=True)
    trade_add.add_argument("--size", type=float, required=True)
    trade_add.add_argument("--stop", type=float)
    trade_add.add_argument("--tp", type=float)
    trade_add.add_argument("--model", default="walkforward_calibrated_macro_intel_v4")
    trade_add.add_argument("--prob", type=float)
    trade_add.add_argument("--notes", default="")
    trade_stats = sub.add_parser("trade-stats", help="show realized manual trading statistics")
    trade_stats.add_argument("--model")
    trade_stats.add_argument("--limit", type=int)

    args = parser.parse_args()
    settings = load_settings()
    if getattr(args, "paper", False):
        settings = settings.__class__(**{**settings.__dict__, "mode": Mode.PAPER})
    if getattr(args, "live", False):
        settings = settings.__class__(**{**settings.__dict__, "mode": Mode.LIVE})

    if args.command == "init-db":
        Storage(settings.db_path).init()
        print(f"Database initialized at {settings.db_path}")
        return
    if args.command == "status":
        rows = Storage(settings.db_path).latest_plans(args.limit)
        if not rows:
            print("No trade plans yet.")
            return
        for row in rows:
            print(
                f"#{row['id']} {row['created_at']} {row['inst_id']} {row['side']} "
                f"approved={bool(row['approved'])} entry={row['entry']:.4f} "
                f"size={row['size']:.8f} reason={row['reason']}"
            )
        return
    if args.command == "dashboard":
        path = render_dashboard(Storage(settings.db_path), Path(args.out))
        render_portal(Path("data/index.html"))
        print(f"Dashboard written to {path.resolve()}")
        return
    if args.command == "portal":
        path = render_portal(Path(args.out))
        print(f"Portal written to {path.resolve()}")
        return
    if args.command == "backtest":
        client = OkxClient(settings.okx_api_key, settings.okx_api_secret, settings.okx_api_passphrase, settings.okx_simulated)
        candles = client.public_candles(settings.inst_id, settings.bar, limit=args.limit)
        strategy = DonchianBreakoutStrategy(
            settings.donchian_lookback,
            settings.atr_period,
            settings.atr_multiplier,
            settings.ema_fast,
            settings.ema_slow,
        )
        risk = RiskEngine(
            RiskLimits(
                settings.risk_per_trade,
                settings.daily_loss_limit,
                settings.max_drawdown,
                settings.max_leverage,
                settings.max_notional_pct,
                settings.max_trades_per_day,
            )
        )
        result = Backtester(strategy, risk, settings.initial_equity, args.max_hold_bars, args.fee_rate).run(settings.inst_id, candles)
        print(f"Backtest {settings.inst_id} {settings.bar}")
        print(f"starting_equity={result.starting_equity:.2f}")
        print(f"ending_equity={result.ending_equity:.2f}")
        print(f"total_return={result.total_return * 100:.2f}%")
        print(f"max_drawdown={result.max_drawdown * 100:.2f}%")
        print(f"trades={len(result.trades)} win_rate={result.win_rate * 100:.2f}% profit_factor={result.profit_factor:.2f}")
        for trade in result.trades[-10:]:
            print(
                f"{trade.entry_ts} {trade.side} entry={trade.entry:.2f} "
                f"exit={trade.exit:.2f} size={trade.size:.6f} pnl={trade.pnl:.2f}"
            )
        return
    if args.command == "analyze":
        client = OkxClient(settings.okx_api_key, settings.okx_api_secret, settings.okx_api_passphrase, settings.okx_simulated)
        macro = None if args.no_macro else MacroAnalyzer().analyze()
        if macro is not None:
            print(f"Macro regime: {macro.label} score={macro.risk_score:.2f} {macro.reason}")
        intel = None if args.no_intel else ExternalIntelAnalyzer().analyze()
        if intel is not None:
            print(f"External intel: {intel.label} score={intel.score:.2f} {intel.reason}")
        storage = Storage(settings.db_path)
        stats = storage.manual_trade_stats(model="walkforward_calibrated_macro_intel_v4")
        if stats["trades"]:
            print(
                "Real calibration "
                f"trades={stats['trades']} win_rate={stats['win_rate'] * 100:.1f}% "
                f"avg_r={stats['avg_r']:.2f} profit_factor={stats['profit_factor']:.2f}"
            )
        else:
            print("Real calibration: no manual trades recorded yet")
        analyzer = MarketAnalyzer(settings.donchian_lookback, settings.atr_period, settings.atr_multiplier, macro=macro, intel=intel)
        opportunities = []
        scan_ids = tuple(dict.fromkeys(settings.inst_ids + settings.stock_inst_ids))
        for inst_id in scan_ids:
            try:
                candles = client.public_candles(inst_id, settings.bar, limit=args.limit)
            except OkxError as exc:
                print(f"Skip {inst_id}: {exc}")
                continue
            opportunities.extend(analyzer.analyze(inst_id, candles))
        ranked = sorted(opportunities, key=lambda item: item.score, reverse=True)
        threshold = settings.min_success_probability if args.min_success is None else args.min_success
        qualified = [
            item
            for item in ranked
            if item.success_probability >= threshold
            and item.expected_r > 0
            and item.feature_quality >= 0.5
            and item.walk_forward_samples >= settings.min_walk_forward_samples
            and item.brier_score is not None
            and item.brier_score <= settings.max_brier_score
            and item.drift_status == "stable"
        ]
        displayed = ranked if args.show_all else qualified
        if not displayed:
            print(
                f"No qualified opportunities: min_success={threshold * 100:.1f}%, "
                f"walk_forward>={settings.min_walk_forward_samples}, "
                f"brier<={settings.max_brier_score:.2f}, drift=stable. "
                "Use --show-all to inspect rejected candidates."
            )
        else:
            print(summarize_opportunities(displayed, args.top))
        path = render_analysis_report(
            displayed[: args.top],
            Path(args.html),
            min_success=threshold,
            macro=macro,
            intel=intel,
            calibration=stats,
        )
        render_portal(Path("data/index.html"))
        print(f"Analysis report written to {path.resolve()}")
        return
    if args.command == "trade-add":
        trade_id = Storage(settings.db_path).record_manual_trade(
            inst_id=args.inst,
            side=args.side,
            entry=args.entry,
            exit_price=args.exit,
            size=args.size,
            stop=args.stop,
            take_profit=args.tp,
            model=args.model,
            estimated_probability=args.prob,
            notes=args.notes,
        )
        print(f"Recorded manual trade #{trade_id}")
        return
    if args.command == "trade-stats":
        storage = Storage(settings.db_path)
        stats = storage.manual_trade_stats(model=args.model, limit=args.limit)
        if not stats["trades"]:
            print("No manual trades recorded yet.")
            return
        print(f"trades={stats['trades']}")
        print(f"real_win_rate={stats['win_rate'] * 100:.2f}%")
        print(f"avg_r={stats['avg_r']:.3f}")
        print(f"profit_factor={stats['profit_factor']:.3f}")
        if stats["avg_estimated_probability"] is not None:
            print(f"avg_model_probability={stats['avg_estimated_probability'] * 100:.2f}%")
            print(f"calibration_error={stats['calibration_error'] * 100:.2f}%")
        for row in storage.latest_manual_trades(5):
            print(
                f"#{row['id']} {row['inst_id']} {row['side']} entry={row['entry']:.4f} "
                f"exit={row['exit']:.4f} pnl={row['pnl']:.4f} R={row['r_multiple']:.2f}"
            )
        return
    if args.command == "doctor":
        checks = run_doctor(settings)
        failed = False
        for check in checks:
            marker = "OK" if check.ok else "FAIL"
            print(f"{marker} {check.name}: {check.detail}")
            failed = failed or not check.ok
        if failed:
            raise SystemExit(1)
        return
    if args.command == "pause":
        settings.pause_file.parent.mkdir(parents=True, exist_ok=True)
        settings.pause_file.write_text("paused\n", encoding="utf-8")
        print(f"Paused. Created {settings.pause_file}")
        return
    if args.command == "resume":
        if settings.pause_file.exists():
            settings.pause_file.unlink()
            print(f"Resumed. Removed {settings.pause_file}")
        else:
            print("Already resumed.")
        return
    if args.command == "run":
        Runner(settings).run(once=args.once)
    if args.command == "scan":
        Runner(settings).scan()


if __name__ == "__main__":
    main()
