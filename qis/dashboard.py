from __future__ import annotations

import html
import json
from pathlib import Path

from qis.storage import Storage


def render_dashboard(storage: Storage, output: Path) -> Path:
    rows = storage.latest_plans(25)
    metrics = _metrics(rows)
    payload = json.dumps(
        [
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "inst_id": row["inst_id"],
                "side": row["side"],
                "entry": row["entry"],
                "stop": row["stop"],
                "take_profit": row["take_profit"],
                "size": row["size"],
                "notional": row["notional"],
                "risk_amount": row["risk_amount"],
                "approved": bool(row["approved"]),
                "reason": row["reason"],
                "signal_reason": row["signal_reason"],
            }
            for row in rows
        ],
        ensure_ascii=True,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(_html(metrics, payload), encoding="utf-8")
    return output


def _metrics(rows: list) -> dict[str, str]:
    approved = sum(1 for row in rows if row["approved"])
    rejected = len(rows) - approved
    risk = sum(float(row["risk_amount"]) for row in rows if row["approved"])
    notional = sum(float(row["notional"]) for row in rows if row["approved"])
    return {
        "plans": str(len(rows)),
        "approved": str(approved),
        "rejected": str(rejected),
        "risk": f"{risk:.2f}",
        "notional": f"{notional:.2f}",
    }


def _html(metrics: dict[str, str], payload: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>QIS 风控记录</title>
  <style>
    :root {{
      --ink: #172026;
      --muted: #64717d;
      --line: #d9e0e6;
      --paper: #f7f4ef;
      --panel: #ffffff;
      --green: #127a52;
      --red: #b63d32;
      --blue: #235f9c;
      --amber: #a16207;
      --shadow: 0 12px 28px rgba(23, 32, 38, .08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      background: var(--paper);
      font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    header {{
      padding: 28px 32px 18px;
      border-bottom: 1px solid var(--line);
      background: #fffaf2;
    }}
    h1 {{ margin: 0; font-size: 28px; letter-spacing: 0; }}
    .sub {{ margin-top: 8px; color: var(--muted); font-size: 14px; }}
    main {{ padding: 24px 32px 40px; max-width: 1280px; margin: 0 auto; }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(5, minmax(130px, 1fr));
      gap: 12px;
      margin-bottom: 20px;
    }}
    .metric {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      box-shadow: var(--shadow);
    }}
    .label {{ color: var(--muted); font-size: 12px; }}
    .value {{ margin-top: 8px; font-size: 24px; font-weight: 700; }}
    .toolbar {{
      display: flex;
      gap: 8px;
      align-items: center;
      justify-content: space-between;
      margin: 18px 0 12px;
    }}
    input {{
      width: min(360px, 100%);
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px 12px;
      font-size: 14px;
      background: white;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: white;
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      box-shadow: var(--shadow);
    }}
    th, td {{
      padding: 12px 10px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      font-size: 13px;
      vertical-align: top;
    }}
    th {{ color: var(--muted); font-size: 12px; background: #f2f6f8; }}
    tr:last-child td {{ border-bottom: 0; }}
    .tag {{
      display: inline-flex;
      align-items: center;
      min-width: 68px;
      justify-content: center;
      border-radius: 999px;
      padding: 4px 8px;
      font-weight: 700;
      font-size: 12px;
    }}
    .buy {{ color: var(--green); background: #e8f5ef; }}
    .sell {{ color: var(--red); background: #faecea; }}
    .ok {{ color: var(--blue); background: #e8f0f9; }}
    .no {{ color: var(--amber); background: #fff3d7; }}
    .reason {{ color: var(--muted); max-width: 360px; line-height: 1.45; }}
    @media (max-width: 820px) {{
      header, main {{ padding-left: 16px; padding-right: 16px; }}
      .metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      table {{ display: block; overflow-x: auto; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>风控记录</h1>
    <div class="sub">展示最近交易计划、风控通过状态、名义仓位和单笔风险。</div>
  </header>
  <main>
    <section class="metrics">
      <div class="metric"><div class="label">计划数</div><div class="value">{html.escape(metrics["plans"])}</div></div>
      <div class="metric"><div class="label">已通过</div><div class="value">{html.escape(metrics["approved"])}</div></div>
      <div class="metric"><div class="label">已拒绝</div><div class="value">{html.escape(metrics["rejected"])}</div></div>
      <div class="metric"><div class="label">风险金额 USDT</div><div class="value">{html.escape(metrics["risk"])}</div></div>
      <div class="metric"><div class="label">名义仓位 USDT</div><div class="value">{html.escape(metrics["notional"])}</div></div>
    </section>
    <div class="toolbar">
      <strong>最近交易计划</strong>
      <input id="filter" placeholder="按币种、方向、原因筛选">
    </div>
    <table>
      <thead>
        <tr>
          <th>ID</th><th>时间</th><th>币种</th><th>方向</th><th>状态</th>
          <th>入场</th><th>止损</th><th>数量</th><th>风险</th><th>原因</th>
        </tr>
      </thead>
      <tbody id="rows"></tbody>
    </table>
  </main>
  <script>
    const plans = {payload};
    const rows = document.getElementById('rows');
    const filter = document.getElementById('filter');
    function fmt(n) {{ return Number(n || 0).toLocaleString(undefined, {{ maximumFractionDigits: 4 }}); }}
    function render() {{
      const q = filter.value.toLowerCase();
      rows.innerHTML = plans
        .filter(p => JSON.stringify(p).toLowerCase().includes(q))
        .map(p => `
          <tr>
            <td>#${{p.id}}</td>
            <td>${{p.created_at}}</td>
            <td>${{p.inst_id}}</td>
            <td><span class="tag ${{p.side}}">${{p.side === 'buy' ? '做多' : '做空'}}</span></td>
            <td><span class="tag ${{p.approved ? 'ok' : 'no'}}">${{p.approved ? '通过' : '拒绝'}}</span></td>
            <td>${{fmt(p.entry)}}</td>
            <td>${{fmt(p.stop)}}</td>
            <td>${{fmt(p.size)}}</td>
            <td>${{fmt(p.risk_amount)}}</td>
            <td class="reason">${{p.reason}}<br>${{p.signal_reason}}</td>
          </tr>`).join('');
    }}
    filter.addEventListener('input', render);
    render();
  </script>
</body>
</html>
"""
