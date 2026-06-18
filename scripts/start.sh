#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="$ROOT_DIR/data"
PYTHON_BIN="${PYTHON_BIN:-python3}"
WEB_HOST="${QIS_WEB_HOST:-127.0.0.1}"
WEB_PORT="${QIS_WEB_PORT:-8787}"

mkdir -p "$DATA_DIR"
cd "$ROOT_DIR"

is_running() {
  local pid_file="$1"
  [[ -f "$pid_file" ]] || return 1
  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

start_detached() {
  local pid_file="$1"
  local log_file="$2"
  shift 2
  "$PYTHON_BIN" - "$ROOT_DIR" "$pid_file" "$log_file" "$@" <<'PY'
import pathlib
import subprocess
import sys

root = pathlib.Path(sys.argv[1])
pid_file = pathlib.Path(sys.argv[2])
log_file = pathlib.Path(sys.argv[3])
command = sys.argv[4:]
log_file.parent.mkdir(parents=True, exist_ok=True)
with log_file.open("ab", buffering=0) as log:
    process = subprocess.Popen(
        command,
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
pid_file.write_text(str(process.pid), encoding="utf-8")
print(process.pid)
PY
}

echo "[1/5] 生成中文看盘入口"
"$PYTHON_BIN" -m qis dashboard
"$PYTHON_BIN" -m qis portal

echo "[2/5] 启动 paper 行情监控"
if is_running "$DATA_DIR/qis-run.pid"; then
  echo "监控已运行，PID $(cat "$DATA_DIR/qis-run.pid")"
else
  rm -f "$DATA_DIR/qis-run.pid"
  pid="$(start_detached "$DATA_DIR/qis-run.pid" "$DATA_DIR/qis-run.log" \
    "$PYTHON_BIN" -u -m qis run --paper)"
  echo "监控已启动，PID $pid"
fi

echo "[3/5] 启动本地网页服务"
if is_running "$DATA_DIR/web.pid"; then
  echo "网页服务已运行，PID $(cat "$DATA_DIR/web.pid")"
else
  rm -f "$DATA_DIR/web.pid"
  pid="$(start_detached "$DATA_DIR/web.pid" "$DATA_DIR/web.log" \
    "$PYTHON_BIN" -u -m http.server "$WEB_PORT" --bind "$WEB_HOST")"
  echo "网页服务已启动，PID $pid"
fi

echo "[4/5] 后台刷新宏观、资讯和全部标的"
if is_running "$DATA_DIR/refresh.pid"; then
  echo "报告刷新正在运行，PID $(cat "$DATA_DIR/refresh.pid")"
else
  rm -f "$DATA_DIR/refresh.pid"
  pid="$(start_detached "$DATA_DIR/refresh.pid" "$DATA_DIR/refresh.log" \
    "$PYTHON_BIN" -u -m qis analyze --top 30 --show-all)"
  echo "报告刷新已启动，PID $pid"
fi

echo "[5/5] 后台运行系统自检"
if is_running "$DATA_DIR/doctor.pid"; then
  echo "系统自检正在运行，PID $(cat "$DATA_DIR/doctor.pid")"
else
  rm -f "$DATA_DIR/doctor.pid"
  pid="$(start_detached "$DATA_DIR/doctor.pid" "$DATA_DIR/doctor.log" \
    "$PYTHON_BIN" -u -m qis doctor)"
  echo "系统自检已启动，PID $pid"
fi

echo
echo "QIS 已启动（paper 模式，不会真实下单）"
echo "看盘地址: http://$WEB_HOST:$WEB_PORT/data/index.html"
echo "查看状态: bash scripts/status.sh"
echo "停止系统: bash scripts/stop.sh"
