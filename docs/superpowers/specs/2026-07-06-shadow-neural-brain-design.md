# Shadow Neural Brain v1 Design

## Goal

Add a neural-network-style shadow learner to QIS without replacing the current
rule-based radar. The new brain runs beside the existing system, predicts,
records its own quality, and only becomes visible as evidence. It must not place
orders or override buy/sell advice in v1.

## Product Shape

The feature is called `神经网络影子大脑 / Shadow Neural Brain`. It gives each
asset a shadow-only forecast:

- direction for the next 5 daily candles: `up`, `down`, or `neutral`
- expected 5-day return
- drawdown risk estimate
- confidence score
- recent validation quality
- projection gate: `allowed`, `watch`, or `blocked`

The UI should present this as a reliability layer, not a trade signal. When data
is weak or validation is poor, it must say `不可推演` instead of forcing a
prediction.

## Architecture

QIS keeps the existing pipeline:

```text
market data -> spot forecast -> dashboard/API -> manual decisions
```

v1 adds a pure-Python learner after forecast calibration:

```text
forecast histories -> feature factory -> tiny MLP -> validation -> shadow score
```

The learner is intentionally small and dependency-free. It uses a deterministic
one-hidden-layer neural classifier over rolling daily features, with a simple
return estimator and validation gate. This keeps startup reliable on the local
Mac and avoids introducing a heavy ML runtime before the data proves value.

## Components

### `qis/ml_shadow.py`

Owns the neural learner and public helpers:

- builds rolling samples from each forecast history
- trains a tiny deterministic MLP on all available asset samples
- validates on holdout samples
- predicts the latest window for each asset
- attaches `shadow_brain` payloads to forecast dictionaries
- ranks assets by validated shadow reliability

### `qis/__main__.py`

Calls the shadow learner during `spot-dashboard` / `spot-watch` after existing
forecast calibration. The rendered `data/spot_forecasts.json` therefore carries
shadow predictions without adding request-time model training.

### `qis/web_server.py`

Exposes `GET /api/shadow-brain/rank`, returning the cached all-asset shadow
ranking. The endpoint must stay fast and read only cached forecast data.

### `qis/deep_analysis.py`

Includes the selected forecast's `shadow_brain` payload in deep-analysis output
so the deep-analysis dialog can show neural status beside the rule-based
super-brain.

### `qis/spot_dashboard.py`

Adds a compact shadow-brain section in the deep-analysis dialog and a shadow
ranking action on the opportunity radar. The design should match the existing
QIS dark, dense, table-first tool style.

## Data Contract

Each forecast may contain:

```json
{
  "shadow_brain": {
    "model_version": "shadow_mlp_v1",
    "status": "shadow_running",
    "direction": "up",
    "up_probability": 0.58,
    "expected_return_5d": 0.021,
    "drawdown_risk_5d": 0.034,
    "confidence": 0.46,
    "samples": 180,
    "validation_samples": 54,
    "validation_accuracy": 0.56,
    "baseline_accuracy": 0.51,
    "edge": 0.05,
    "projection_gate": "watch",
    "reason": "影子运行，优势尚未稳定"
  }
}
```

For weak data:

```json
{
  "shadow_brain": {
    "model_version": "shadow_mlp_v1",
    "status": "insufficient_data",
    "projection_gate": "blocked",
    "reason": "至少需要 90 根日K"
  }
}
```

## Learning And Safety Rules

- v1 trains from cached daily histories only.
- v1 runs in shadow mode only.
- The model cannot auto-buy, auto-sell, or replace `decision`.
- The gate is conservative:
  - fewer than 90 candles: `blocked`
  - validation edge below 3 percentage points: `watch`
  - validation edge at or above 3 points and confidence above 0.55: `allowed`
- UI labels must make the shadow nature explicit.

## Testing

Add tests for:

- sample generation and prediction from synthetic histories
- insufficient-history blocking
- ranking by shadow reliability
- deep-analysis payload includes shadow brain
- web endpoint returns cached shadow ranking
- dashboard template contains shadow UI hooks

## Non-Goals

- No PyTorch, TensorFlow, or external ML dependency in v1.
- No automatic trading.
- No online mutation of a long-lived model file.
- No claim that shadow accuracy is real until validation proves it.
