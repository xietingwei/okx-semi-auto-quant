from __future__ import annotations

import html
from pathlib import Path

from qis.analyzer import Opportunity
from qis.external_intel import ExternalIntel
from qis.macro import MacroRegime


def render_analysis_report(
    opportunities: list[Opportunity],
    output: Path,
    min_success: float = 0.70,
    macro: MacroRegime | None = None,
    intel: ExternalIntel | None = None,
    calibration: dict[str, float | int | None] | None = None,
) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(_html(opportunities, min_success, macro, intel, calibration), encoding="utf-8")
    return output


def _html(
    opportunities: list[Opportunity],
    min_success: float,
    macro: MacroRegime | None,
    intel: ExternalIntel | None,
    calibration: dict[str, float | int | None] | None,
) -> str:
    rows = "\n".join(
        _row(index, item)
        for index, item in enumerate(sorted(opportunities, key=lambda row: row.score, reverse=True), start=1)
    )
    best = max(opportunities, key=lambda row: row.score) if opportunities else None
    headline = (
        f"{html.escape(best.inst_id)} {best.side.value.upper()} {best.success_probability * 100:.1f}%"
        if best
        else "No Qualified Setup"
    )
    macro_text = "macro disabled" if macro is None else f"{macro.label} score={macro.risk_score:.2f}; {macro.reason}"
    intel_text = "intel disabled" if intel is None else f"{intel.label} score={intel.score:.2f}; {intel.reason}"
    calibration_text = _calibration_text(calibration)
    headline_rows = "\n".join(
        f"<li>{html.escape(item.source)}: <a href=\"{html.escape(item.link)}\">{html.escape(item.title)}</a></li>"
        for item in (intel.headlines[:8] if intel else [])
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>QIS Opportunity Report</title>
  <style>
    :root {{
      --ink:#18212a; --muted:#63717f; --line:#d9e1e8; --paper:#f5f7f4;
      --panel:#ffffff; --green:#0f7a55; --red:#b5423b; --blue:#265f9f; --gold:#8a650e;
      --shadow:0 12px 28px rgba(24,33,42,.08);
    }}
    * {{ box-sizing:border-box; }}
    body {{
      margin:0; background:var(--paper); color:var(--ink);
      font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    header {{
      padding:28px 32px 18px; background:#ffffff; border-bottom:1px solid var(--line);
    }}
    h1 {{ margin:0; font-size:28px; letter-spacing:0; }}
    .sub {{ margin-top:8px; color:var(--muted); }}
    main {{ max-width:1320px; margin:0 auto; padding:24px 32px 44px; }}
    .summary {{
      display:grid; grid-template-columns: 1.4fr repeat(3, 1fr); gap:12px; margin-bottom:18px;
    }}
    .metric {{
      background:var(--panel); border:1px solid var(--line); border-radius:8px;
      padding:16px; box-shadow:var(--shadow);
    }}
    .label {{ color:var(--muted); font-size:12px; text-transform:uppercase; }}
    .value {{ margin-top:8px; font-size:24px; font-weight:750; }}
    table {{
      width:100%; border-collapse:collapse; background:white; border:1px solid var(--line);
      border-radius:8px; overflow:hidden; box-shadow:var(--shadow);
    }}
    th,td {{ padding:12px 10px; border-bottom:1px solid var(--line); text-align:left; font-size:13px; }}
    th {{ background:#edf3f5; color:var(--muted); font-size:12px; }}
    tr:last-child td {{ border-bottom:0; }}
    .tag {{
      display:inline-flex; justify-content:center; min-width:68px; border-radius:999px;
      padding:4px 8px; font-size:12px; font-weight:750;
    }}
    .buy {{ color:var(--green); background:#e6f4ed; }}
    .sell {{ color:var(--red); background:#faeceb; }}
    .active {{ color:var(--blue); background:#e8f0fa; }}
    .watch {{ color:var(--gold); background:#fff3d8; }}
    .reason {{ max-width:320px; color:var(--muted); line-height:1.45; }}
    .small {{ color:var(--muted); font-size:12px; }}
    @media (max-width:880px) {{
      header, main {{ padding-left:16px; padding-right:16px; }}
      .summary {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
      table {{ display:block; overflow-x:auto; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>QIS Opportunity Report</h1>
    <div class="sub">similarity_bayes_macro_intel_v3；默认只展示成功率不低于 {min_success * 100:.1f}% 的候选。概率是统计估计，不是保证。</div>
  </header>
  <main>
    <section class="summary">
      <div class="metric"><div class="label">Top Setup</div><div class="value">{headline}</div></div>
      <div class="metric"><div class="label">Candidates</div><div class="value">{len(opportunities)}</div></div>
      <div class="metric"><div class="label">Active</div><div class="value">{sum(1 for item in opportunities if item.status == "active")}</div></div>
      <div class="metric"><div class="label">Macro</div><div class="value" style="font-size:14px">{html.escape(macro_text)}</div></div>
      <div class="metric"><div class="label">External Intel</div><div class="value" style="font-size:14px">{html.escape(intel_text)}</div></div>
      <div class="metric"><div class="label">Real Calibration</div><div class="value" style="font-size:14px">{html.escape(calibration_text)}</div></div>
    </section>
    <section class="metric" style="margin-bottom:18px">
      <div class="label">Referenced Headlines</div>
      <ul style="margin:10px 0 0; padding-left:18px; color:var(--muted); line-height:1.5">{headline_rows}</ul>
    </section>
    <table>
      <thead>
        <tr>
          <th>Rank</th><th>Symbol</th><th>Side</th><th>Status</th><th>Entry Zone</th>
          <th>Stop</th><th>TP1</th><th>TP2</th><th>Success</th><th>Sample</th>
          <th>Quality</th><th>ExpR</th><th>Score</th><th>Regime</th><th>Macro</th><th>Intel</th><th>Model</th><th>Reason</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
  </main>
</body>
</html>
"""


def _calibration_text(calibration: dict[str, float | int | None] | None) -> str:
    if not calibration or not calibration.get("trades"):
        return "no manual trades yet"
    win_rate = calibration.get("win_rate")
    avg_r = calibration.get("avg_r")
    trades = calibration.get("trades")
    profit_factor = calibration.get("profit_factor")
    return f"{trades} trades; real win {float(win_rate) * 100:.1f}%; avgR {float(avg_r):.2f}; PF {float(profit_factor):.2f}"


def _row(index: int, item: Opportunity) -> str:
    rank_note = " low sample" if item.sample_size < 8 else ""
    return f"""
        <tr>
          <td class="small">#{index}{rank_note}</td>
          <td>{html.escape(item.inst_id)}</td>
          <td><span class="tag {item.side.value}">{item.side.value.upper()}</span></td>
          <td><span class="tag {item.status}">{item.status.upper()}</span></td>
          <td>{item.entry_low:.4f} - {item.entry_high:.4f}</td>
          <td>{item.stop:.4f}</td>
          <td>{item.take_profit_1:.4f}</td>
          <td>{item.take_profit_2:.4f}</td>
          <td>{item.success_probability * 100:.1f}%</td>
          <td>{item.sample_size}</td>
          <td>{item.feature_quality:.2f}</td>
          <td>{item.expected_r:.2f}</td>
          <td>{item.score:.1f}</td>
          <td>{html.escape(item.regime)}</td>
          <td>{html.escape(item.macro_label)} {item.macro_score:.2f}</td>
          <td>{html.escape(item.intel_label)} {item.intel_score:.2f}</td>
          <td>{html.escape(item.model)}</td>
          <td class="reason">{html.escape(item.reason)}</td>
        </tr>"""
