# Agent memory for QIS handoff

Last updated: 2026-07-21.

## Product intent

QIS is a local-first OKX semi-auto quant decision system. It analyzes spot and
swap markets plus external US-stock daily data, presents a manual decision
dashboard, records human trade decisions, monitors positions, and can ask an
OpenAI-compatible LLM for decision context. It must remain
analysis/manual-registration only by default; do not add automatic trading from
the web UI without explicit user direction.

## User preferences and recurring pain points

- The user wants a functional product, not decorative descriptions. Remove or
  avoid unnecessary UI copy.
- Chinese is the default UI language; English is supported through the existing
  i18n dictionary in `qis/spot_dashboard.py`.
- The user checks behavior in the browser and is sensitive to “button clicked
  but nothing happened.” Prefer immediate UI feedback and non-blocking refreshes.
- When the user says an endpoint “没用 / 没通”, verify the actual API path and
  runtime process, not just the frontend event handler.
- Restart the local service after backend or dashboard-template changes when
  validating real behavior.
- Deep analysis should present evidence-backed daily hypotheses and validation
  status, not unsupported one-shot certainty. The “super brain” is a pattern
  library built from tested daily hypotheses. Future scenarios should only be
  driven by core patterns; weak or rejected current patterns must downgrade the
  forecast to low-confidence observation instead of pretending to predict.

## Recent system fixes

- Dashboard i18n was added with Chinese default and English toggle.
- Buy form and assistant streaming were fixed so buttons are direct `type=button`
  actions and assistant functions are exposed on `window`.
- Buy persistence was hardened for SQLite concurrency.
- `/api/spot/positions` and assistant context generation were made lighter:
  holdings use targeted forecast snapshots and cached calibration instead of
  recomputing the whole radar on every sentinel refresh.
- A delete-record flow now exists for spot position records:
  - storage: `Storage.delete_spot_position`
  - API: `POST /api/spot/delete`
  - UI: “删除记录 / Delete Record” buttons in position cards and trade log
- The dashboard detail page has a “深度分析 / Deep Analysis” action backed by
  `GET /api/deep-analysis`. It reviews up to 180 recent daily candles when the
  source has enough history, attaches external news when available, validates
  each hypothesis on later price action, and summarizes repeated patterns as the
  super-brain mode library. Forecast caches keep up to 200 daily candles so the
  180-day review has enough prior-day context; newly listed symbols may still
  show shorter actual coverage. If an OKX stock-mapped symbol has short history
  but a same-symbol Yahoo Finance US-stock forecast exists, the deep-analysis
  API uses the longer Yahoo daily history while preserving the selected
  instrument id in the response.
- The opportunity radar has an “全部深度分析 / All Deep Analysis” ranking action
  backed by `GET /api/deep-analysis/rank`. Ranking is reliability-first:
  projection-ready symbols with core patterns outrank weak or rejected patterns;
  do not rank raw all-sample hit rate ahead of core validation quality.
- Shadow Neural Brain v1 is implemented as a dependency-free, pure-Python
  shadow learner in `qis/ml_shadow.py`. `spot-watch` attaches `shadow_brain`
  payloads to cached forecasts; `GET /api/shadow-brain/rank` returns the cached
  reliability ranking; deep-analysis output includes the selected asset's
  shadow payload. It is shadow-only and must not replace manual trade decisions
  or trigger automatic orders.
- Polymarket public market data is integrated as a short-term, read-only event
  evidence layer in `qis/polymarket.py`. `spot-watch` fetches markets resolving
  inside 14 days, applies transparent order-book/spread/volume/liquidity gates,
  attaches only direct asset events and explicit sector mappings under each
  forecast's `polymarket` key. Macro and geopolitical markets may be collected
  for shadow research but must never fill an unrelated asset's empty event list.
  SQLite stores at most one snapshot per market per UTC hour for future shadow
  validation. This evidence must not alter forecast values, opportunity scores,
  strategy gates, or order execution.
- The asset detail chart is a professional, range-aware candlestick terminal.
  `GET /api/spot/candles` accepts `range=1D|1M|3M|6M|1Y|ALL`; crypto ranges use
  5m, 4H, 12H, and paginated daily OKX history as appropriate. The UI separates
  forecast horizons from chart ranges, supports zoom/pan/crosshair interaction,
  main-chart MA/EMA/BOLL/SAR/SuperTrend/Ichimoku overlays, and VOL/MACD/RSI/KDJ/
  StochRSI/ATR/CCI/WR/OBV lower panes. External equities remain daily-only and
  must not present fabricated intraday candles.

## Key API endpoints

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/spot/positions` | `GET` | Returns spot positions, sentinel risk analyses, trade stats, model evaluation, strategy adjustments, advice, and latest learning run. |
| `/api/spot/quotes` | `GET` | Returns live-rebased opportunity radar forecasts. |
| `/api/spot/candles` | `GET` | Returns range-aware OKX or external-equity candles for the selected instrument. |
| `/api/deep-analysis` | `GET` | Returns selected-symbol daily reviews, hypothesis validations, scenarios, and super-brain patterns. |
| `/api/deep-analysis/rank` | `GET` | Ranks all cached symbols by deep-analysis reliability, core validation rate, and sample depth. |
| `/api/shadow-brain/rank` | `GET` | Ranks cached symbols by shadow neural validation edge, confidence, and projection gate. |
| `/api/polymarket/events` | `GET` | Returns the selected symbol's cached, read-only event intelligence and shadow-validation state. |
| `/api/spot/buy` | `POST` | Opens a manual spot position record. |
| `/api/spot/sell` | `POST` | Closes a manual spot position record with realized PnL. |
| `/api/spot/delete` | `POST` | Permanently deletes a manual spot position record. |
| `/api/assistant/status` | `GET` | Returns LLM configuration status. |
| `/api/assistant/stream` | `POST` | Streams assistant responses as SSE. |

## Verification habits

Use focused tests first, then full tests:

```bash
python3 -m pytest tests/test_storage.py tests/test_spot_forecast.py tests/test_deep_analysis.py tests/test_web_server.py -q
python3 -m pytest -q
git diff --check
git status --short
```

For real runtime checks, use `bash scripts/status.sh`, restart with
`bash scripts/stop.sh` and `bash scripts/start.sh`, then call local endpoints.
If a test creates manual position records in `data/qis.sqlite3`, use a unique
`codex-verification-*` note and delete that row after verification.

## Watch-outs

- `data/index.html` is generated; source edits belong in `qis/spot_dashboard.py`.
- `data/qis.sqlite3`, logs, PID files, and generated dashboards are runtime
  artifacts. Do not stage them.
- The dashboard template is a large raw HTML string; keep changes small and add
  template assertions in `tests/test_spot_forecast.py`.
- SQLite can lock under concurrent background refreshes. Prefer short storage
  transactions and avoid repeating schema initialization in hot paths.
- `/api/spot/quotes` and `/api/spot/positions` must stay fast; avoid full model
  recomputation in request handlers.
- US-stock opportunities use `QIS_US_STOCK_SYMBOLS` and Yahoo Finance daily
  candles via `qis/us_stocks.py`; the UI should identify the data source,
  exchange, and broker/platform hint because these are not OKX spot quotes.
- Assistant failures may be due to LLM/network configuration, but the UI should
  still stream a clear error event and re-enable the send button.
- Current launch scripts bind the web app to `127.0.0.1:8787`.
- QIS now has a vn.py-style headless platform kernel. New exchange/data-source
  access should be implemented as a gateway under `qis/gateway/`; independent
  business capabilities should be apps under `qis/app/`; process composition
  belongs in `qis/runtime.py`. Do not reintroduce direct `OkxClient`
  construction into CLI/service flows. See `docs/VNPY_ARCHITECTURE.md`.
