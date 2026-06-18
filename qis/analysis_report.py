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
    best_side = "做多" if best and best.side.value == "buy" else "做空"
    headline = (
        f"{html.escape(best.inst_id)} {best_side} {best.success_probability * 100:.1f}%"
        if best
        else "暂无合格机会"
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
  <title>QIS 决策中心</title>
  <style>
    :root {{
      --ink:#17212b; --muted:#657482; --line:#d9e1e7; --paper:#f3f5f6;
      --panel:#ffffff; --green:#087a55; --red:#b33d36; --blue:#205d99; --gold:#946600;
      --soft:#edf2f5; --shadow:0 8px 22px rgba(24,33,42,.07);
    }}
    * {{ box-sizing:border-box; }}
    body {{
      margin:0; background:var(--paper); color:var(--ink);
      font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    header {{
      padding:22px 28px 16px; background:#ffffff; border-bottom:1px solid var(--line);
    }}
    h1 {{ margin:0; font-size:28px; letter-spacing:0; }}
    .sub {{ margin-top:8px; color:var(--muted); }}
    main {{ max-width:1480px; margin:0 auto; padding:18px 24px 36px; }}
    .summary {{
      display:grid; grid-template-columns: 1.2fr repeat(5, minmax(150px, 1fr)); gap:10px; margin-bottom:14px;
    }}
    .metric {{
      background:var(--panel); border:1px solid var(--line); border-radius:6px;
      padding:14px; box-shadow:var(--shadow);
    }}
    .label {{ color:var(--muted); font-size:12px; }}
    .value {{ margin-top:8px; font-size:24px; font-weight:750; }}
    .controls {{
      display:flex; gap:8px; align-items:center; flex-wrap:wrap; padding:12px 0;
    }}
    .controls input, .controls select {{
      height:36px; border:1px solid var(--line); border-radius:6px; background:white;
      color:var(--ink); padding:0 10px; font-size:13px;
    }}
    .controls input {{ width:min(320px,100%); }}
    .table-wrap {{ overflow:auto; max-height:68vh; border:1px solid var(--line); border-radius:6px; }}
    table {{
      width:100%; border-collapse:collapse; background:white; border:1px solid var(--line);
      box-shadow:var(--shadow); white-space:nowrap;
    }}
    th,td {{ padding:12px 10px; border-bottom:1px solid var(--line); text-align:left; font-size:13px; }}
    th {{ position:sticky; top:0; z-index:2; background:#eaf0f3; color:#536473; font-size:12px; }}
    tbody tr:hover {{ background:#f7fafb; }}
    tr:last-child td {{ border-bottom:0; }}
    .tag {{
      display:inline-flex; justify-content:center; min-width:68px; border-radius:999px;
      padding:4px 8px; font-size:12px; font-weight:750;
    }}
    .buy {{ color:var(--green); background:#e6f4ed; }}
    .sell {{ color:var(--red); background:#faeceb; }}
    .active {{ color:var(--blue); background:#e8f0fa; }}
    .watch {{ color:var(--gold); background:#fff3d8; }}
    .stable {{ color:var(--green); background:#e5f4ed; }}
    .warning {{ color:var(--gold); background:#fff3d8; }}
    .drift {{ color:var(--red); background:#fae9e7; }}
    .insufficient {{ color:var(--muted); background:#edf1f3; }}
    .reason {{ max-width:320px; color:var(--muted); line-height:1.45; }}
    .small {{ color:var(--muted); font-size:12px; }}
    @media (max-width:880px) {{
      header, main {{ padding-left:14px; padding-right:14px; }}
      .summary {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>量化决策中心</h1>
    <div class="sub">模型：walkforward_calibrated_macro_intel_v4。概率经过历史前推验证与可靠性校准；模型漂移或验证不足时禁止交易。</div>
  </header>
  <main>
    <section class="summary">
      <div class="metric"><div class="label">诊断排序首位</div><div class="value">{headline}</div></div>
      <div class="metric"><div class="label">候选数量</div><div class="value">{len(opportunities)}</div></div>
      <div class="metric"><div class="label">已触发</div><div class="value">{sum(1 for item in opportunities if item.status == "active")}</div></div>
      <div class="metric"><div class="label">宏观环境</div><div class="value" style="font-size:14px">{html.escape(macro_text)}</div></div>
      <div class="metric"><div class="label">外部资讯</div><div class="value" style="font-size:14px">{html.escape(intel_text)}</div></div>
      <div class="metric"><div class="label">实盘校准</div><div class="value" style="font-size:14px">{html.escape(calibration_text)}</div></div>
    </section>
    <section class="metric" style="margin-bottom:18px">
      <div class="label">参考资讯标题</div>
      <ul style="margin:10px 0 0; padding-left:18px; color:var(--muted); line-height:1.5">{headline_rows}</ul>
    </section>
    <div class="controls">
      <input id="search" placeholder="搜索标的，例如 BTC、NVDA">
      <select id="asset"><option value="">全部类别</option><option value="stock">股票类</option><option value="crypto">加密货币</option></select>
      <select id="direction"><option value="">全部方向</option><option value="buy">做多</option><option value="sell">做空</option></select>
      <select id="health"><option value="">全部模型状态</option><option value="stable">稳定</option><option value="warning">警告</option><option value="drift">漂移</option><option value="insufficient">验证不足</option></select>
    </div>
    <div class="table-wrap"><table>
      <thead>
        <tr>
          <th>排名</th><th>类别</th><th>标的</th><th>方向</th><th>状态</th><th>入场区间</th>
          <th>止损</th><th>止盈1</th><th>止盈2</th><th>原始概率</th><th>校准概率</th>
          <th>前推样本</th><th>Brier</th><th>校准误差</th><th>模型状态</th>
          <th>期望R</th><th>评分</th><th>结构</th><th>宏观</th><th>资讯</th><th>原因</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table></div>
  </main>
  <script>
    const controls = ['search','asset','direction','health'].map(id => document.getElementById(id));
    function filterRows() {{
      const query = document.getElementById('search').value.toLowerCase();
      const asset = document.getElementById('asset').value;
      const direction = document.getElementById('direction').value;
      const health = document.getElementById('health').value;
      document.querySelectorAll('tbody tr').forEach(row => {{
        const visible = (!query || row.dataset.symbol.includes(query))
          && (!asset || row.dataset.asset === asset)
          && (!direction || row.dataset.direction === direction)
          && (!health || row.dataset.health === health);
        row.style.display = visible ? '' : 'none';
      }});
    }}
    controls.forEach(control => control.addEventListener('input', filterRows));
  </script>
</body>
</html>
"""


def _calibration_text(calibration: dict[str, float | int | None] | None) -> str:
    if not calibration or not calibration.get("trades"):
        return "暂无手动交易样本"
    win_rate = calibration.get("win_rate")
    avg_r = calibration.get("avg_r")
    trades = calibration.get("trades")
    profit_factor = calibration.get("profit_factor")
    return f"{trades} 笔；真实胜率 {float(win_rate) * 100:.1f}%；平均R {float(avg_r):.2f}；盈亏因子 {float(profit_factor):.2f}"


def _row(index: int, item: Opportunity) -> str:
    rank_note = " 样本少" if item.sample_size < 8 else ""
    side_text = "做多" if item.side.value == "buy" else "做空"
    status_text = "已触发" if item.status == "active" else "观察"
    health_text = {
        "stable": "稳定",
        "warning": "警告",
        "drift": "漂移",
        "insufficient": "验证不足",
    }.get(item.drift_status, item.drift_status)
    brier_text = f"{item.brier_score:.3f}" if item.brier_score is not None else "-"
    calibration_text = f"{item.calibration_error:.3f}" if item.calibration_error is not None else "-"
    return f"""
        <tr data-symbol="{html.escape(item.inst_id.lower())}" data-asset="{item.asset_class}" data-direction="{item.side.value}" data-health="{item.drift_status}">
          <td class="small">#{index}{rank_note}</td>
          <td>{'股票类' if item.asset_class == 'stock' else '加密货币'}</td>
          <td>{html.escape(item.inst_id)}</td>
          <td><span class="tag {item.side.value}">{side_text}</span></td>
          <td><span class="tag {item.status}">{status_text}</span></td>
          <td>{item.entry_low:.4f} - {item.entry_high:.4f}</td>
          <td>{item.stop:.4f}</td>
          <td>{item.take_profit_1:.4f}</td>
          <td>{item.take_profit_2:.4f}</td>
          <td>{item.raw_probability * 100:.1f}%</td>
          <td>{item.success_probability * 100:.1f}%</td>
          <td>{item.walk_forward_samples}</td>
          <td>{brier_text}</td>
          <td>{calibration_text}</td>
          <td><span class="tag {item.drift_status}">{health_text}</span></td>
          <td>{item.expected_r:.2f}</td>
          <td>{item.score:.1f}</td>
          <td>{html.escape(item.regime)}</td>
          <td>{html.escape(item.macro_label)} {item.macro_score:.2f}</td>
          <td>{html.escape(item.intel_label)} {item.intel_score:.2f}</td>
          <td class="reason">{html.escape(item.reason)}</td>
        </tr>"""
