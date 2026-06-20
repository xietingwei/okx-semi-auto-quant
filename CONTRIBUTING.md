# Contributing to QIS

Thank you for helping improve QIS.

## Development setup

```bash
git clone https://github.com/xietingwei/okx-semi-auto-quant.git
cd okx-semi-auto-quant
cp .env.example .env
python3 -m pytest -q
```

Public market tests do not require private OKX credentials.

## Before opening a pull request

Run:

```bash
python3 -m pytest -q
python3 -m compileall -q qis tests
git diff --check
```

Keep changes focused and avoid committing files under `data/`, `.env`, account
details, logs, or generated dashboards.

## Strategy and model changes

A strategy contribution should document:

1. the market hypothesis;
2. the intended direction and horizon;
3. the market regime where it should work;
4. the known failure mode;
5. parameter bounds and risk controls;
6. how look-ahead bias is prevented;
7. tests that compare the new behavior with the previous behavior.

New strategies must use an isolated model version and must remain simulation
only until enough strategy-specific outcomes have matured.

## Pull request guidance

- Explain the user-visible outcome first.
- Include tests for bugs and behavioral changes.
- Preserve paper mode as the default.
- Do not add automatic order execution to the web interface.
- Call out schema or migration changes explicitly.

By contributing, you confirm that you have the right to submit the work. No
license is currently granted for this repository; contribution acceptance does
not change that status.
