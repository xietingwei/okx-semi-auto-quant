# QIS project structure

This repository is intentionally kept as a small Python package plus local runtime
scripts. Avoid large directory moves unless there is a clear migration plan,
because the launch scripts and generated dashboard paths are deliberately simple.

## Top-level layout

| Path | Purpose | Notes |
| --- | --- | --- |
| `qis/` | Application package | Forecasting, storage, web API, UI template, risk logic, assistant context. |
| `tests/` | Pytest test suite | Add regression tests for every source behavior change. |
| `scripts/` | Operator scripts | `start.sh`, `stop.sh`, and `status.sh` manage local background processes. |
| `docs/` | Maintainer and agent handoff docs | Keep durable project knowledge here instead of burying it in chat history. |
| `data/` | Runtime state and generated dashboards | Do not edit or commit generated HTML, logs, SQLite, PID files, or cache files. |
| `.codex/` | Local Codex hooks | Existing automation; avoid changing without checking the hook behavior. |
| `.githooks/` | Git hooks | Pre-commit runs tests; keep it fast and deterministic. |

## Important source files

| File | Responsibility |
| --- | --- |
| `qis/__main__.py` | CLI entrypoint for analyze, web, spot-watch, doctor, and related commands. |
| `qis/web_server.py` | Local HTTP API and static dashboard server. Spot buy/sell/delete endpoints live here. |
| `qis/spot_dashboard.py` | Source template for `data/index.html`; edit this file, not generated HTML. |
| `qis/storage.py` | SQLite schema and persistence helpers for positions, manual trades, forecast evaluations, and learning runs. |
| `qis/spot_forecast.py` | Spot forecast model, strategy variants, opportunity scoring, and model versioning. |
| `qis/short_term.py` | Canonical candle cleanup, short-term data quality scoring, and evidence-gate context. |
| `qis/ml_shadow.py` | Dependency-free shadow neural learner, validation gate, and all-asset shadow ranking. |
| `qis/polymarket.py` | Public Polymarket client, event quality gates, asset mapping, cache, and shadow-intelligence payloads. |
| `qis/deep_analysis.py` | Per-symbol daily deep analysis, hypothesis validation, and super-brain pattern summaries. |
| `qis/position_risk.py` | Holding-level sentinel analysis: stops, target distance, risk score, and sell timing. |
| `qis/decision_assistant.py` | OpenAI-compatible LLM request/streaming and decision-context construction. |
| `qis/forecast_learning.py` | Walk-forward calibration and adjustment application. |
| `qis/okx.py` | OKX REST client wrappers. |
| `qis/event/` | Event-driven queue dispatch, timer, and handler isolation. |
| `qis/trader/` | Main engine, gateway/app contracts, lifecycle, event names, and DTOs. |
| `qis/gateway/` | Protocol adapters; the OKX gateway publishes domain events. |
| `qis/app/` | Pluggable market-data and short-term strategy/risk engines. |
| `qis/runtime.py` | Composition root for MainEngine, gateways, event bus, and apps. |
| `qis/us_stocks.py` | Yahoo Finance daily-candle client for external US stock opportunity candidates. |
| `qis/config.py` | Environment-backed runtime settings. |

The external benchmark and resulting priorities are recorded in
[`docs/PROFESSIONAL_REVIEW.md`](PROFESSIONAL_REVIEW.md).

## Runtime flow

```text
scripts/start.sh
  ├─ python3 -m qis spot-watch
  │    ├─ creates QisRuntime → MainEngine → OkxGateway/MarketDataApp
  │    ├─ fetches OKX/public market data through the gateway
  │    ├─ fetches qualified short-term Polymarket event evidence
  │    ├─ attaches shadow neural predictions to cached forecasts
  │    ├─ writes data/spot_forecasts.json
  │    └─ renders data/index.html from qis/spot_dashboard.py
  ├─ python3 -m qis web
  │    ├─ owns a persistent event-driven runtime and shared OKX gateway
  │    ├─ serves http://127.0.0.1:8787/
  │    ├─ exposes /api/spot/positions, /buy, /sell, /delete
  │    ├─ exposes /api/deep-analysis for selected-symbol daily reviews
  │    ├─ exposes /api/deep-analysis/rank for all-symbol reliability ranking
  │    ├─ exposes /api/shadow-brain/rank for shadow neural reliability ranking
  │    ├─ exposes /api/polymarket/events for selected-symbol event evidence
  │    └─ streams /api/assistant/stream
  └─ python3 -m qis doctor
```

The rationale and migration map are documented in
[`docs/VNPY_ARCHITECTURE.md`](VNPY_ARCHITECTURE.md).

## Editing guidelines

- Treat `qis/spot_dashboard.py` as the canonical frontend source.
- Runtime files under `data/` can be used for verification, but source changes
  should not be made there.
- Keep API changes small and covered by tests in `tests/`.
- Preserve paper/manual-only behavior; do not add automatic order execution to
  the dashboard.
- Before finishing source or doc changes, run:

```bash
git diff --check
python3 -m pytest -q
git status --short
```

## Local operations

```bash
bash scripts/start.sh
bash scripts/status.sh
bash scripts/stop.sh
```

The web app is expected at `http://127.0.0.1:8787/`.
