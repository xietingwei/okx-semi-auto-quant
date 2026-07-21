# QIS professional benchmark review

This review records the external references used to shape QIS as a short-term,
evidence-first decision terminal. It is intentionally a benchmark, not a claim
that QIS should copy or embed another trading engine.

## Reference projects and standards

- [OKX V5 market-data API](https://app.okx.com/docs-v5/en/) defines recent and
  historical candle endpoints separately, caps a recent-candle response at 300
  rows, exposes exact exchange intervals, and marks whether a candle is complete.
  QIS therefore merges the live edge with paginated history, preserves exact
  timestamps, and never relabels daily fallback data as an intraday series.
- [Freqtrade](https://github.com/freqtrade/freqtrade) treats dry-run, historical
  data download, backtesting, plotting, and strategy validation as separate
  operator workflows. Its
  [lookahead analysis](https://docs.freqtrade.io/en/stable/lookahead-analysis/)
  explicitly checks that indicators and signals do not consume future candles.
  QIS keeps paper/manual operation as the default and generates walk-forward
  samples only from data available at each forecast origin.
- [Jesse](https://github.com/jesse-ai/jesse) emphasizes multi-timeframe research,
  no-lookahead backtests, explicit risk management, detailed metrics, and Monte
  Carlo robustness checks. QIS follows the first four principles now; stress
  testing forecast decisions across perturbed paths remains future work.
- [NautilusTrader](https://github.com/nautechsystems/nautilus_trader) uses the
  same deterministic time and execution semantics in research and live systems.
  QIS is not an execution engine, but should continue reducing differences
  between cached analysis, live quote rebasing, and historical evaluation.
- [TradingView Lightweight Charts panes](https://tradingview.github.io/lightweight-charts/docs/panes)
  keep price and secondary studies on independent visual panes. QIS follows
  that layout: candles and overlays remain in the main pane while volume,
  momentum, and volatility studies use a separate scale.
- [Polymarket market-data overview](https://docs.polymarket.com/market-data/overview)
  separates event discovery from public CLOB prices and order books. Its
  [resolution documentation](https://docs.polymarket.com/concepts/resolution)
  also makes clear that a market probability is tied to a precisely defined
  outcome and resolution process. QIS therefore displays the event question,
  price, spread, liquidity, and resolution time together instead of treating a
  probability as a context-free asset forecast.

## Decisions applied

1. Forecasts stop at 14 days: 3 days is primary, 7 days confirms, 1 day helps
   execution timing, and 14 days is risk context only.
2. A signal is observation-only when daily data is stale, duplicated, gapped,
   or too shallow, or when 3-day/7-day sample-out validation has not beaten a
   simple baseline.
3. Historical chart ranges and forecast horizons are separate concepts. Crypto
   charts support real OKX intervals and range-aware pagination; external equity
   data remains explicitly daily-only.
4. Synthetic trust scores are not shown or returned. Deep-analysis ranking uses
   transparent fields: core sample count, core hit rate, and projection state.
5. The terminal remains paper/manual-only. Research metrics cannot authorize an
   order or bypass position risk controls.
6. Forecast calibration is scoped to instrument plus model version and is
   idempotent across live-price refreshes. Failed validation is presented as
   "no validated direction" with a realized-volatility envelope; shrunken audit
   values remain stored for evaluation but are not promoted as point forecasts.
7. Polymarket is a public, read-only event evidence layer. Only markets inside
   the 14-day forecast boundary that pass order-book, spread, volume, liquidity,
   and non-terminal-probability gates are displayed. Asset detail pages only
   receive direct asset events or explicit sector mappings; macro and risk
   events never fill an otherwise empty asset. Hourly snapshots are kept for
   future shadow validation; event probabilities cannot modify forecast values,
   scores, strategy gates, or execution.

## Next engineering priorities

1. Persist OKX candle completion state instead of relying only on a conservative
   closed-candle boundary.
2. Add an automated lookahead audit that compares full-history indicator output
   against every truncated forecast origin.
3. Add path perturbation and trade-order Monte Carlo tests before promoting any
   short-term strategy from observation to actionable.
4. Move live market updates to a reconnecting WebSocket feed while retaining
   REST reconciliation and gap repair.
5. Measure research/live parity by recording the exact data revision, model
   version, interval, and feature timestamp used for every displayed decision.
6. After enough event snapshots mature, measure whether probability changes add
   incremental out-of-sample information beyond price, volatility, and macro
   baselines before considering any model feature experiment.
