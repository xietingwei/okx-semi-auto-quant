from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import time

from qis.analysis_report import render_analysis_report
from qis.analyzer import MarketAnalyzer, summarize_opportunities
from qis.backtest import Backtester
from qis.config import load_settings
from qis.dashboard import render_dashboard
from qis.deepseek_intel import DeepSeekIntelProvider, DeepSeekSettings
from qis.doctor import run_doctor
from qis.email_alerts import notify_opportunities
from qis.external_intel import ExternalIntelAnalyzer
from qis.forecast_learning import apply_strategy_adjustments, hour_bucket
from qis.macro import MacroAnalyzer
from qis.market_factors import build_market_contexts, global_market_environment, market_context
from qis.models import Mode
from qis.okx import OkxClient, OkxError
from qis.portal import render_portal
from qis.polymarket import (
    PolymarketClient,
    PolymarketError,
    build_asset_intelligence,
    collect_event_catalog,
    load_catalog,
    save_catalog,
)
from qis.risk import RiskEngine, RiskLimits
from qis.runner import Runner
from qis.runtime import QisRuntime, create_runtime
from qis.storage import Storage
from qis.spot_dashboard import render_spot_dashboard
from qis.spot_forecast import SpotForecastEngine
from qis.short_term import canonicalize_candles
from qis.ml_shadow import attach_shadow_brain
from qis.strategy import DonchianBreakoutStrategy
from qis.us_stocks import YahooFinanceClient, UsStockError, UsStockHistory
from qis.trader.object import HistoryRequest
from qis.web_server import serve


STABLE_BASES = {"USDT", "USDC", "USD", "DAI", "TUSD", "FDUSD", "USDP", "EURT"}
EQUITY_SYMBOLS = {"AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "TSLA"}


def _discover_spot_ids(client: OkxClient, settings) -> tuple[str, ...]:
    configured = list(settings.spot_inst_ids)
    if settings.spot_auto_discover:
        instruments = {
            item.get("instId"): item
            for item in client.public_instruments("SPOT")
            if item.get("state") == "live" and item.get("quoteCcy") == "USDT"
        }
        tickers = client.public_tickers("SPOT")
        ranked = []
        for ticker in tickers:
            inst_id = ticker.get("instId")
            instrument = instruments.get(inst_id)
            if not instrument:
                continue
            base = instrument.get("baseCcy", "")
            if base in STABLE_BASES or base.endswith(("3L", "3S", "5L", "5S")):
                continue
            try:
                volume = float(ticker.get("volCcy24h") or 0)
            except ValueError:
                volume = 0.0
            ranked.append((volume, inst_id))
        ranked.sort(reverse=True)
        configured.extend(inst_id for _, inst_id in ranked[: settings.spot_max_assets])
    configured.extend(settings.stock_inst_ids)
    return tuple(dict.fromkeys(configured))


def _render_spot(settings, output: Path) -> None:
    with create_runtime(settings) as runtime:
        _render_spot_with_runtime(settings, output, runtime)


def _render_spot_with_runtime(
    settings,
    output: Path,
    runtime: QisRuntime,
) -> None:
    client = runtime.okx
    engine = SpotForecastEngine()
    storage = Storage(settings.db_path)
    forecasts = []
    strategy_suites = {}
    okx_inst_ids = _discover_spot_ids(client, settings)
    ticker_map = _ticker_map(client)
    candles_by_inst = {}
    def fetch_candles(inst_id: str):
        try:
            candles = runtime.market_data.query_history(
                HistoryRequest(inst_id, "1D", 300)
            )
            return inst_id, candles, None
        except OkxError as exc:
            return inst_id, None, exc

    with ThreadPoolExecutor(max_workers=8) as executor:
        for inst_id, candles, error in executor.map(fetch_candles, okx_inst_ids):
            if error is not None:
                print(f"Skip {inst_id}: {error}", flush=True)
            elif candles:
                candles_by_inst[inst_id] = candles
    stock_histories = _fetch_us_stock_histories(settings.us_stock_symbols)
    for history in stock_histories.values():
        candles_by_inst[history.inst_id] = history.candles
    macro = MacroAnalyzer().analyze()
    environment = global_market_environment(
        tuple(inst_id for inst_id in okx_inst_ids if inst_id in candles_by_inst),
        ticker_map,
        candles_by_inst,
    )
    contexts = build_market_contexts(
        client,
        tuple(inst_id for inst_id in okx_inst_ids if inst_id in candles_by_inst),
        ticker_map,
        candles_by_inst,
        macro,
        environment,
        Path("data/market_factors.json"),
    )
    for history in stock_histories.values():
        contexts[history.inst_id] = market_context(
            book={},
            funding={},
            ticker={
                "last": str(history.candles[-1].close),
                "open24h": str(history.candles[-2].close),
            },
            candles=history.candles,
            macro=macro,
            environment=environment,
            open_interest=0.0,
            open_interest_change=0.0,
            open_interest_history_available=False,
        )
    for inst_id, candles in candles_by_inst.items():
        ticker = ticker_map.get(inst_id, {})
        stock_history = stock_histories.get(inst_id)
        try:
            live_price = (
                stock_history.candles[-1].close
                if stock_history
                else float(ticker.get("last") or 0) or None
            )
            quote_time = (
                stock_history.candles[-1].ts
                if stock_history
                else (
                datetime.fromtimestamp(int(ticker["ts"]) / 1000, tz=timezone.utc)
                if ticker.get("ts")
                else None
                )
            )
        except (TypeError, ValueError):
            live_price, quote_time = None, None
        suite = engine.analyze_suite(
            inst_id,
            candles,
            live_price=live_price,
            quote_time=quote_time,
            market_context=contexts.get(inst_id),
        )
        if suite:
            forecast = engine.analyze(
                inst_id,
                candles,
                live_price=live_price,
                quote_time=quote_time,
                market_context=contexts.get(inst_id),
            )
        else:
            forecast = None
        if forecast:
            forecast_body = asdict(forecast)
            if stock_history:
                forecast_body.update(
                    {
                        "market_type": "美股现货",
                        "quote_source": stock_history.quote_source,
                        "data_source": stock_history.quote_source,
                        "exchange": stock_history.exchange,
                        "trade_platform": stock_history.trade_platform,
                    }
                )
                suite = [
                    {
                        **variant,
                        "market_type": "美股现货",
                        "quote_source": stock_history.quote_source,
                        "data_source": stock_history.quote_source,
                        "exchange": stock_history.exchange,
                        "trade_platform": stock_history.trade_platform,
                    }
                    for variant in suite
                ]
                forecasts.append(forecast_body)
            else:
                forecasts.append(forecast)
            strategy_suites[inst_id] = suite
            if not storage.has_forecast_history(inst_id):
                _backfill_forecast_history(storage, engine, inst_id, candles)
    if not forecasts:
        raise RuntimeError("no spot forecasts available")
    observed_at = max(
        datetime.fromisoformat(item["quote_time"] if isinstance(item, dict) else item.quote_time)
        for item in forecasts
    )
    evaluated_count = storage.evaluate_due_forecasts(
        {
            (item["inst_id"] if isinstance(item, dict) else item.inst_id): (
                item["current_price"] if isinstance(item, dict) else item.current_price
            )
            for item in forecasts
        },
        observed_at=observed_at,
    )
    adjustments = storage.forecast_strategy_adjustments()
    evaluation = storage.forecast_evaluation()
    advice = storage.forecast_advice()
    predicted_at = hour_bucket(observed_at)
    calibrated_forecasts = []
    for forecast in forecasts:
        raw = forecast if isinstance(forecast, dict) else asdict(forecast)
        forecast_inst_id = str(raw["inst_id"])
        asset_adjustments = storage.forecast_strategy_adjustments(
            inst_id=forecast_inst_id,
        )
        calibrated = apply_strategy_adjustments(raw, asset_adjustments)
        calibrated_variants = []
        for variant in strategy_suites.get(forecast_inst_id, []):
            variant_adjustments = storage.forecast_strategy_adjustments(
                model_version=str(variant["model_version"]),
                inst_id=forecast_inst_id,
            )
            calibrated_variant = apply_strategy_adjustments(
                variant,
                variant_adjustments,
            )
            calibrated_variants.append(calibrated_variant)
            storage.record_forecast_snapshot(
                calibrated_variant,
                predicted_at=predicted_at,
            )
        calibrated["strategy_variants"] = calibrated_variants
        calibrated_forecasts.append(calibrated)
        storage.record_forecast_snapshot(calibrated, predicted_at=predicted_at)
    storage.record_forecast_learning_run(
        predicted_at,
        evaluated_count,
        evaluation,
        adjustments,
        advice,
    )
    calibrated_forecasts = _attach_polymarket_intelligence(
        settings,
        storage,
        calibrated_forecasts,
    )
    calibrated_forecasts = attach_shadow_brain(calibrated_forecasts)
    path = render_spot_dashboard(calibrated_forecasts, output)
    print(f"Spot dashboard written to {path.resolve()} ({len(forecasts)} assets)", flush=True)
    try:
        notified = notify_opportunities(calibrated_forecasts, settings)
        if notified:
            print(f"Email opportunity alert sent ({notified} candidates)", flush=True)
    except Exception as exc:
        print(f"Email opportunity alert failed: {exc}", flush=True)


def _attach_polymarket_intelligence(
    settings,
    storage: Storage,
    forecasts: list[dict],
) -> list[dict]:
    now = datetime.now(timezone.utc)
    inst_ids = [str(item["inst_id"]) for item in forecasts]
    cache_path = settings.db_path.parent / "polymarket_events.json"
    source_state = "disabled"
    source_error = ""
    catalog: list[dict] = []
    updated_at = now
    if settings.polymarket_enabled:
        try:
            catalog = collect_event_catalog(
                PolymarketClient(settings.polymarket_timeout_seconds),
                now=now,
                horizon_days=settings.polymarket_horizon_days,
                min_liquidity=settings.polymarket_min_liquidity,
                min_volume_24h=settings.polymarket_min_volume_24h,
                max_spread=settings.polymarket_max_spread,
            )
            source_state = "live"
            save_catalog(cache_path, catalog, now)
            print(
                "Polymarket event intelligence: "
                f"{sum(bool(item.get('eligible')) for item in catalog)} qualified / "
                f"{len(catalog)} relevant markets",
                flush=True,
            )
        except (PolymarketError, OSError) as exc:
            source_error = str(exc)
            cached, captured_at = load_catalog(cache_path)
            catalog = _current_cached_events(
                cached,
                now,
                settings.polymarket_horizon_days,
            )
            if catalog:
                source_state = "cache"
                updated_at = captured_at or now
                print(f"Polymarket degraded to cached events: {exc}", flush=True)
            else:
                source_state = "unavailable"
                print(f"Polymarket event intelligence unavailable: {exc}", flush=True)
        storage.record_polymarket_snapshots(catalog, captured_at=now)
    stats = storage.polymarket_snapshot_stats()
    intelligence = build_asset_intelligence(
        catalog,
        inst_ids,
        updated_at=updated_at,
        max_events=settings.polymarket_max_events,
        snapshot_stats=stats,
        source_state=source_state,
        source_error=source_error,
    )
    return [
        {**forecast, "polymarket": intelligence[str(forecast["inst_id"])]}
        for forecast in forecasts
    ]


def _current_cached_events(
    events: list[dict],
    now: datetime,
    horizon_days: int,
) -> list[dict]:
    latest = now.timestamp() + max(1, horizon_days) * 86_400
    current = []
    for event in events:
        try:
            end_at = datetime.fromisoformat(
                str(event.get("end_at") or "").replace("Z", "+00:00")
            )
        except ValueError:
            continue
        if end_at.tzinfo is None:
            end_at = end_at.replace(tzinfo=timezone.utc)
        if now.timestamp() < end_at.timestamp() <= latest:
            current.append(event)
    return current


def _fetch_us_stock_histories(symbols: tuple[str, ...]) -> dict[str, UsStockHistory]:
    client = YahooFinanceClient()
    histories = {}

    def fetch(symbol: str):
        try:
            return client.daily_history(symbol), None
        except UsStockError as exc:
            return None, exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        for history, error in executor.map(fetch, tuple(dict.fromkeys(symbols))):
            if error is not None:
                print(f"Skip US stock {error}", flush=True)
            elif history is not None:
                histories[history.inst_id] = history
    return histories


def _ticker_map(client: OkxClient) -> dict[str, dict]:
    rows: list[dict] = []
    for inst_type in ("SPOT", "SWAP"):
        try:
            rows.extend(client.public_tickers(inst_type))
        except OkxError as exc:
            print(f"Ticker refresh degraded for {inst_type}: {exc}", flush=True)
    return {str(item.get("instId")): item for item in rows if item.get("instId")}


def _scan_opportunities(settings, args, analyzer: MarketAnalyzer) -> list:
    opportunities = []
    scan_ids = tuple(dict.fromkeys(settings.inst_ids + settings.stock_inst_ids))
    with create_runtime(settings) as runtime:
        for inst_id in scan_ids:
            try:
                candles = runtime.market_data.query_history(
                    HistoryRequest(inst_id, settings.bar, args.limit)
                )
            except OkxError as exc:
                print(f"Skip {inst_id}: {exc}")
                continue
            opportunities.extend(analyzer.analyze(inst_id, candles))
    return opportunities


def _backfill_forecast_history(
    storage: Storage,
    engine: SpotForecastEngine,
    inst_id: str,
    candles: list,
) -> None:
    ordered = canonicalize_candles(candles)
    closed = ordered[:-1] if len(ordered) > 1 else ordered
    horizon_days = dict((key, days) for key, _, days in engine.HORIZONS)
    # Short-horizon models need several distinct walk-forward dates. Four-day
    # spacing supplies dense 1/3/7/14-day outcomes without treating hourly
    # snapshots as independent evidence.
    start = max(90, len(closed) - 240)
    # Keep only origins with a complete 14-day outcome for every short-term
    # horizon. Partial tails would over-represent 1d/3d samples in calibration.
    end = len(closed) - max(horizon_days.values())
    for origin in range(start, max(start, end), 4):
        historical = engine.analyze(inst_id, ordered[: origin + 2])
        if historical is None:
            continue
        actual_prices = {
            key: closed[origin + days].close
            for key, days in horizon_days.items()
            if origin + days < len(closed)
        }
        if not actual_prices:
            continue
        predicted_at = closed[origin].ts
        storage.record_historical_forecast_outcome(
            asdict(historical),
            predicted_at,
            actual_prices,
        )


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
    spot_dashboard = sub.add_parser("spot-dashboard", help="render spot multi-horizon dashboard")
    spot_dashboard.add_argument("--out", default="data/index.html")
    spot_watch = sub.add_parser("spot-watch", help="continuously refresh spot dashboard")
    spot_watch.add_argument("--out", default="data/index.html")
    spot_watch.add_argument("--interval", type=int, default=900)
    web_parser = sub.add_parser("web", help="serve dashboard and manual trade API")
    web_parser.add_argument("--host", default="127.0.0.1")
    web_parser.add_argument("--port", type=int, default=8787)
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
    if args.command == "spot-dashboard":
        _render_spot(settings, Path(args.out))
        return
    if args.command == "spot-watch":
        while True:
            try:
                _render_spot(settings, Path(args.out))
            except Exception as exc:
                print(f"Spot refresh failed: {exc}", flush=True)
            time.sleep(max(60, args.interval))
    if args.command == "web":
        serve(args.host, args.port, Path("data"), settings.db_path, settings)
        return
    if args.command == "backtest":
        with create_runtime(settings) as runtime:
            candles = runtime.market_data.query_history(
                HistoryRequest(settings.inst_id, settings.bar, args.limit)
            )
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
        macro = None if args.no_macro else MacroAnalyzer().analyze()
        if macro is not None:
            print(f"Macro regime: {macro.label} score={macro.risk_score:.2f} {macro.reason}")
        intel = None if args.no_intel else ExternalIntelAnalyzer().analyze()
        if intel is not None:
            if settings.deepseek_api_key:
                provider = DeepSeekIntelProvider(
                    DeepSeekSettings(
                        api_key=settings.deepseek_api_key,
                        base_url=settings.deepseek_base_url,
                        model=settings.deepseek_model,
                        timeout_seconds=settings.deepseek_timeout_seconds,
                        cache_ttl_seconds=settings.deepseek_cache_ttl_seconds,
                    )
                )
                all_inst_ids = tuple(dict.fromkeys(settings.inst_ids + settings.stock_inst_ids))
                intel = provider.enrich(intel, all_inst_ids)
            print(f"External intel: {intel.label} score={intel.score:.2f} {intel.reason}")
            print(f"Intel provider: {intel.provider}; summary={intel.research_summary or 'n/a'}")
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
        opportunities = _scan_opportunities(settings, args, analyzer)
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
        with Runner(settings) as runner:
            runner.run(once=args.once)
    if args.command == "scan":
        with Runner(settings) as runner:
            runner.scan()


if __name__ == "__main__":
    main()
