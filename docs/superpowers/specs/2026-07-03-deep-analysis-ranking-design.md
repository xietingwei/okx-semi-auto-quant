# Deep Analysis Ranking Design

## Goal

Add an all-symbol deep analysis ranking that answers: which symbols have the most trustworthy prediction history right now.

## Ranking Definition

The leaderboard must not sort by raw all-sample hit rate alone. A symbol is considered strongly rankable only when its current or historical deep-analysis patterns have passed the core-brain gate:

- core pattern count is greater than zero;
- core validation rate is available from tested core hypotheses;
- projection readiness is true only when the current pattern is usable for projection.

Primary ranking order:

1. projection-ready symbols first;
2. symbols with core patterns before symbols without core patterns;
3. higher core validation rate;
4. higher core tested sample count;
5. higher all-sample validation rate;
6. higher all-sample tested count.

Symbols without core patterns remain visible but are labeled low-confidence observation and must not outrank projection-ready symbols.

## User Experience

The opportunity radar gets an `全部深度分析` action. Clicking it opens a ranking dialog that loads `/api/deep-analysis/rank`, shows a compact leaderboard, and lets the user jump to a symbol detail/deep-analysis view.

The leaderboard columns are rank, symbol, market/source, core hit rate, all-sample hit rate, core patterns, projection state, current scenario, and actions.

## API

`GET /api/deep-analysis/rank?days=180`

Response shape:

```json
{
  "ok": true,
  "ranking": {
    "generated_at": "2026-07-03T00:00:00+00:00",
    "days": 180,
    "total": 89,
    "ranked": [],
    "skipped": []
  }
}
```

The ranking endpoint uses existing forecast cache/live rebasing and does not fetch per-symbol news; news is too slow and not needed for ranking prediction reliability.

## Boundaries

This feature ranks deep-analysis reliability, not trade attractiveness. A high score means the symbol has better validated prediction structure, not that it is automatically a buy.
