# Shadow Neural Brain v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dependency-free neural-network-style shadow learner that predicts, validates, ranks, and displays shadow-only reliability without changing trade decisions.

**Architecture:** Implement a pure-Python tiny MLP in `qis/ml_shadow.py`, attach its output during the existing `spot-watch` refresh, expose cached rankings through the web server, and render compact status blocks in the existing dashboard. The system stays local-first and manual-only.

**Tech Stack:** Python standard library, existing QIS JSON cache, local HTTP server, raw HTML/CSS/JS template, pytest.

---

## File Structure

- Create `qis/ml_shadow.py`: feature extraction, tiny MLP, forecast attachment, ranking.
- Create `tests/test_ml_shadow.py`: model and ranking tests.
- Modify `qis/__main__.py`: attach shadow payloads before rendering the dashboard.
- Modify `qis/web_server.py`: add cached `/api/shadow-brain/rank` endpoint.
- Modify `qis/deep_analysis.py`: pass `shadow_brain` through selected-symbol analysis.
- Modify `qis/spot_dashboard.py`: add shadow dialog/ranking UI hooks.
- Modify `tests/test_deep_analysis.py`, `tests/test_web_server.py`, `tests/test_spot_forecast.py`: regression coverage.
- Modify `README.md`, `README.zh-CN.md`, `docs/AGENT_MEMORY.md`, `docs/PROJECT_STRUCTURE.md`: durable handoff notes.

### Task 1: Shadow Learner Core

**Files:**
- Create: `qis/ml_shadow.py`
- Test: `tests/test_ml_shadow.py`

- [ ] **Step 1: Write failing model tests**

```python
def test_shadow_brain_attaches_prediction_for_sufficient_history() -> None:
    forecasts = [_forecast("TREND-USDT", 180, 0.002)]
    result = attach_shadow_brain(forecasts)
    brain = result[0]["shadow_brain"]
    assert brain["status"] == "shadow_running"
    assert brain["samples"] >= 60
    assert brain["projection_gate"] in {"allowed", "watch", "blocked"}
    assert 0.0 <= brain["up_probability"] <= 1.0


def test_shadow_brain_blocks_short_history() -> None:
    result = attach_shadow_brain([_forecast("SHORT-USDT", 45, 0.002)])
    assert result[0]["shadow_brain"]["status"] == "insufficient_data"
    assert result[0]["shadow_brain"]["projection_gate"] == "blocked"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `/usr/bin/python3 -m pytest tests/test_ml_shadow.py -q`

Expected: fail because `qis.ml_shadow` does not exist.

- [ ] **Step 3: Implement core learner**

Create `qis/ml_shadow.py` with:

```python
MODEL_VERSION = "shadow_mlp_v1"
MIN_HISTORY = 90
WINDOW = 30
HORIZON = 5

def attach_shadow_brain(forecasts: list[dict]) -> list[dict]:
    ...

def rank_shadow_brains(forecasts: list[dict]) -> dict:
    ...
```

The implementation builds rolling features from daily history, trains a tiny
deterministic one-hidden-layer classifier, validates on holdout samples, and
attaches conservative gate fields.

- [ ] **Step 4: Verify green**

Run: `/usr/bin/python3 -m pytest tests/test_ml_shadow.py -q`

Expected: pass.

### Task 2: Refresh Pipeline And API

**Files:**
- Modify: `qis/__main__.py`
- Modify: `qis/web_server.py`
- Test: `tests/test_web_server.py`

- [ ] **Step 1: Write failing route test**

```python
def test_shadow_brain_rank_route_returns_cached_ranking(monkeypatch) -> None:
    handler = QisRequestHandler.__new__(QisRequestHandler)
    handler.path = "/api/shadow-brain/rank"
    payloads = []
    monkeypatch.setattr(QisRequestHandler, "_live_forecasts", lambda self: {
        "A-USDT": {"inst_id": "A-USDT", "symbol": "A", "shadow_brain": {...}}
    })
    monkeypatch.setattr(QisRequestHandler, "_json", lambda self, payload, status=200: payloads.append((status, payload)))
    QisRequestHandler.do_GET(handler)
    assert payloads[0][1]["ok"] is True
```

- [ ] **Step 2: Run test to verify failure**

Run: `/usr/bin/python3 -m pytest tests/test_web_server.py::test_shadow_brain_rank_route_returns_cached_ranking -q`

Expected: fail because the route is missing.

- [ ] **Step 3: Implement pipeline/API**

Import `attach_shadow_brain` in `qis/__main__.py` and apply it before
`render_spot_dashboard`. Import `rank_shadow_brains` in `qis/web_server.py` and
serve `GET /api/shadow-brain/rank` from cached live forecasts.

- [ ] **Step 4: Verify green**

Run: `/usr/bin/python3 -m pytest tests/test_web_server.py tests/test_ml_shadow.py -q`

Expected: pass.

### Task 3: Deep Analysis Integration

**Files:**
- Modify: `qis/deep_analysis.py`
- Test: `tests/test_deep_analysis.py`

- [ ] **Step 1: Write failing deep-analysis test**

```python
def test_deep_analysis_includes_shadow_brain_payload() -> None:
    forecast = _forecast()
    forecast["shadow_brain"] = {"status": "shadow_running", "projection_gate": "watch"}
    result = DeepAnalysisEngine().analyze(forecast, max_days=80)
    assert result["shadow_brain"]["status"] == "shadow_running"
```

- [ ] **Step 2: Run test to verify failure**

Run: `/usr/bin/python3 -m pytest tests/test_deep_analysis.py::test_deep_analysis_includes_shadow_brain_payload -q`

Expected: fail because the output does not include `shadow_brain`.

- [ ] **Step 3: Pass payload through**

Add `"shadow_brain": forecast.get("shadow_brain") or {}` to the deep-analysis
result body.

- [ ] **Step 4: Verify green**

Run: `/usr/bin/python3 -m pytest tests/test_deep_analysis.py -q`

Expected: pass.

### Task 4: Dashboard UI

**Files:**
- Modify: `qis/spot_dashboard.py`
- Test: `tests/test_spot_forecast.py`

- [ ] **Step 1: Write failing template assertions**

Assert rendered HTML contains:

```python
assert 'id="shadowRankBtn"' in html
assert 'id="shadowRankDialog"' in html
assert "/api/shadow-brain/rank" in html
assert "function renderShadowBrain" in html
assert "shadowBrain:'神经网络影子大脑'" in html
```

- [ ] **Step 2: Run test to verify failure**

Run: `/usr/bin/python3 -m pytest tests/test_spot_forecast.py::test_cached_forecasts_rebuild_latest_dashboard_template -q`

Expected: fail on the first missing UI hook.

- [ ] **Step 3: Add compact UI hooks**

Add a radar button, dialog, i18n labels, `renderShadowBrain`, `openShadowRank`,
and a deep-analysis section that renders the selected asset's `shadow_brain`.

- [ ] **Step 4: Verify green**

Run: `/usr/bin/python3 -m pytest tests/test_spot_forecast.py -q`

Expected: pass.

### Task 5: Documentation And Final Verification

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/AGENT_MEMORY.md`
- Modify: `docs/PROJECT_STRUCTURE.md`

- [ ] **Step 1: Update durable docs**

Document that Shadow Neural Brain v1 is shadow-only, dependency-free, cached
during refresh, and exposed through `/api/shadow-brain/rank`.

- [ ] **Step 2: Run focused tests**

Run: `/usr/bin/python3 -m pytest tests/test_ml_shadow.py tests/test_deep_analysis.py tests/test_web_server.py tests/test_spot_forecast.py -q`

Expected: pass.

- [ ] **Step 3: Run full tests and diff checks**

Run:

```bash
/usr/bin/python3 -m pytest -q
git diff --check
git status --short
```

Expected: all tests pass, diff check passes, status shows only intended files.

- [ ] **Step 4: Restart and probe runtime**

Run:

```bash
bash scripts/stop.sh
bash scripts/start.sh
bash scripts/status.sh
```

Probe `http://127.0.0.1:8787/api/shadow-brain/rank` and one
`/api/deep-analysis` response.

- [ ] **Step 5: Commit**

Run:

```bash
git add docs README.md README.zh-CN.md qis tests
git commit -m "Add shadow neural brain"
```

Expected: commit succeeds and repository is clean.

## Self-Review

- Spec coverage: model, refresh, API, deep analysis, UI, docs, tests are covered.
- Placeholder scan: no TBD/TODO placeholders.
- Type consistency: public names are `attach_shadow_brain`, `rank_shadow_brains`, and `shadow_brain`.
