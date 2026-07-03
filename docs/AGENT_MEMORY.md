# Agent memory for QIS handoff

Last updated: 2026-07-03.

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
  library built from tested daily hypotheses.

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
  `GET /api/deep-analysis`. It reviews up to 126 recent daily candles, attaches
  external news when available, validates each hypothesis on later price action,
  and summarizes repeated patterns as the super-brain mode library.

## Key API endpoints

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/spot/positions` | `GET` | Returns spot positions, sentinel risk analyses, trade stats, model evaluation, strategy adjustments, advice, and latest learning run. |
| `/api/spot/quotes` | `GET` | Returns live-rebased opportunity radar forecasts. |
| `/api/spot/candles` | `GET` | Returns OKX candles for the selected instrument and bar. |
| `/api/deep-analysis` | `GET` | Returns selected-symbol daily reviews, hypothesis validations, scenarios, and super-brain patterns. |
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
