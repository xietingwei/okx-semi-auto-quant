# Deep Analysis Ranking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an all-symbol deep analysis leaderboard ranking which symbols have the most trustworthy validated prediction structure.

**Architecture:** Extend `qis/deep_analysis.py` with a pure ranking helper that reuses `DeepAnalysisEngine.analyze()` and produces compact rows. Add a local API route in `qis/web_server.py`, then render the leaderboard from `qis/spot_dashboard.py` using the current dashboard visual language.

**Tech Stack:** Python standard library, local HTTP handler, raw HTML/CSS/JavaScript dashboard template, pytest.

---

### Task 1: Backend Ranking Helper

**Files:**
- Modify: `qis/deep_analysis.py`
- Test: `tests/test_deep_analysis.py`

- [ ] **Step 1: Write failing tests**

Add tests that import `rank_deep_analyses`, assert projection-ready rows outrank low-confidence rows, and assert ranking rows expose core and all-sample accuracy fields.

- [ ] **Step 2: Verify RED**

Run: `/usr/bin/python3 -m pytest tests/test_deep_analysis.py -q`

Expected: fail because `rank_deep_analyses` does not exist yet.

- [ ] **Step 3: Implement minimal ranking helper**

Create `rank_deep_analyses(forecasts, max_days=180)` and private helpers for rank score, current pattern lookup, and sort key.

- [ ] **Step 4: Verify GREEN**

Run: `/usr/bin/python3 -m pytest tests/test_deep_analysis.py -q`

Expected: pass.

### Task 2: API Route

**Files:**
- Modify: `qis/web_server.py`
- Test: `tests/test_web_server.py`

- [ ] **Step 1: Write failing route test**

Add a route-level test or helper-level test proving `/api/deep-analysis/rank` returns `ok: true` and a `ranked` array.

- [ ] **Step 2: Verify RED**

Run: `/usr/bin/python3 -m pytest tests/test_web_server.py -q`

Expected: fail because the route is not implemented.

- [ ] **Step 3: Implement route**

Use `_live_forecasts()` and `rank_deep_analyses()` without fetching news.

- [ ] **Step 4: Verify GREEN**

Run: `/usr/bin/python3 -m pytest tests/test_web_server.py -q`

Expected: pass.

### Task 3: Dashboard Ranking Dialog

**Files:**
- Modify: `qis/spot_dashboard.py`
- Test: `tests/test_spot_forecast.py`

- [ ] **Step 1: Write failing template assertions**

Assert the rendered dashboard contains `deepRankBtn`, `deepRankDialog`, `/api/deep-analysis/rank`, `renderDeepRank`, and Chinese i18n labels.

- [ ] **Step 2: Verify RED**

Run: `/usr/bin/python3 -m pytest tests/test_spot_forecast.py::test_cached_forecasts_rebuild_latest_dashboard_template -q`

Expected: fail because the UI is absent.

- [ ] **Step 3: Implement UI**

Add the radar action button, dialog markup, i18n keys, fetch/render functions, and row action that jumps to detail then opens deep analysis.

- [ ] **Step 4: Verify GREEN**

Run: `/usr/bin/python3 -m pytest tests/test_spot_forecast.py::test_cached_forecasts_rebuild_latest_dashboard_template -q`

Expected: pass.

### Task 4: Final Verification And Runtime

**Files:**
- Modify docs if endpoint memory changes are needed.

- [ ] **Step 1: Run focused verification**

Run: `/usr/bin/python3 -m pytest tests/test_deep_analysis.py tests/test_spot_forecast.py tests/test_web_server.py -q`

- [ ] **Step 2: Run full verification**

Run: `git diff --check && /usr/bin/python3 -m pytest -q && git status --short`

- [ ] **Step 3: Commit**

Commit all relevant source, tests, and docs.

- [ ] **Step 4: Restart and probe runtime**

Run: `PYTHON_BIN=/usr/bin/python3 bash scripts/stop.sh && PYTHON_BIN=/usr/bin/python3 bash scripts/start.sh`

Probe `http://127.0.0.1:8787/api/deep-analysis/rank`.
