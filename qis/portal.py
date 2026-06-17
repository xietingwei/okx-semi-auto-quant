from __future__ import annotations

from pathlib import Path


def render_portal(output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(_html(), encoding="utf-8")
    return output


def _html() -> str:
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>QIS 量化看盘台</title>
  <style>
    :root {
      --ink:#15202b; --muted:#667789; --line:#d8e0e7; --paper:#f4f6f8;
      --panel:#ffffff; --green:#0f7a55; --blue:#225c9d; --red:#b33f36;
      --shadow:0 10px 24px rgba(21,32,43,.08);
    }
    * { box-sizing:border-box; }
    body {
      margin:0; color:var(--ink); background:var(--paper);
      font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    header {
      position:sticky; top:0; z-index:10; background:#ffffff;
      border-bottom:1px solid var(--line); box-shadow:0 4px 16px rgba(21,32,43,.05);
    }
    .bar {
      display:flex; align-items:center; justify-content:space-between; gap:16px;
      padding:14px 18px; max-width:1480px; margin:0 auto;
    }
    .brand { display:flex; align-items:baseline; gap:10px; min-width:240px; }
    .brand strong { font-size:18px; letter-spacing:0; }
    .brand span { color:var(--muted); font-size:12px; }
    nav { display:flex; gap:8px; align-items:center; }
    button {
      border:1px solid var(--line); background:#f8fafb; color:var(--ink);
      border-radius:7px; padding:9px 14px; cursor:pointer; font-weight:700;
      font-size:14px;
    }
    button.active { background:var(--blue); border-color:var(--blue); color:white; }
    .status {
      display:flex; gap:10px; align-items:center; color:var(--muted); font-size:12px;
    }
    .dot { width:8px; height:8px; border-radius:999px; background:var(--green); display:inline-block; }
    main { max-width:1480px; margin:0 auto; padding:14px 18px 22px; }
    .panel {
      background:var(--panel); border:1px solid var(--line); border-radius:8px;
      box-shadow:var(--shadow); overflow:hidden;
    }
    iframe {
      display:none; width:100%; height:calc(100vh - 102px); border:0; background:white;
    }
    iframe.active { display:block; }
    @media (max-width:760px) {
      .bar { flex-direction:column; align-items:stretch; }
      .brand { min-width:0; }
      nav { width:100%; }
      button { flex:1; }
      .status { justify-content:space-between; }
      iframe { height:calc(100vh - 158px); }
    }
  </style>
</head>
<body>
  <header>
    <div class="bar">
      <div class="brand">
        <strong>QIS 量化看盘台</strong>
        <span>OKX 半自动交易系统</span>
      </div>
      <nav aria-label="页面切换">
        <button class="active" data-target="analysis">机会看盘</button>
        <button data-target="risk">风控记录</button>
      </nav>
      <div class="status"><span><i class="dot"></i> 本地服务运行中</span><span id="clock"></span></div>
    </div>
  </header>
  <main>
    <section class="panel">
      <iframe id="analysis" class="active" src="analysis.html" title="机会看盘"></iframe>
      <iframe id="risk" src="dashboard.html" title="风控记录"></iframe>
    </section>
  </main>
  <script>
    const buttons = [...document.querySelectorAll('button[data-target]')];
    const frames = [...document.querySelectorAll('iframe')];
    function activate(id) {
      buttons.forEach(btn => btn.classList.toggle('active', btn.dataset.target === id));
      frames.forEach(frame => frame.classList.toggle('active', frame.id === id));
      location.hash = id;
    }
    buttons.forEach(btn => btn.addEventListener('click', () => activate(btn.dataset.target)));
    if (location.hash === '#risk') activate('risk');
    function tick() {
      document.getElementById('clock').textContent = new Date().toLocaleString('zh-CN', { hour12:false });
    }
    tick();
    setInterval(tick, 1000);
  </script>
</body>
</html>
"""
