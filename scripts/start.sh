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

echo "[1/3] 启动现货预测刷新"
if is_running "$DATA_DIR/spot-watch.pid"; then
  echo "现货预测已运行，PID $(cat "$DATA_DIR/spot-watch.pid")"
else
  rm -f "$DATA_DIR/spot-watch.pid"
  pid="$(start_detached "$DATA_DIR/spot-watch.pid" "$DATA_DIR/spot-watch.log" \
    "$PYTHON_BIN" -u -m qis spot-watch --interval 300)"
  echo "现货预测已启动，PID $pid"
fi

echo "[2/3] 启动现货决策台与交易登记 API"
if is_running "$DATA_DIR/web.pid"; then
  echo "网页服务已运行，PID $(cat "$DATA_DIR/web.pid")"
else
  rm -f "$DATA_DIR/web.pid"
  pid="$(start_detached "$DATA_DIR/web.pid" "$DATA_DIR/web.log" \
    "$PYTHON_BIN" -u -m qis web --host "$WEB_HOST" --port "$WEB_PORT")"
  echo "网页服务已启动，PID $pid"
fi

echo "[3/3] 后台运行系统自检"
if is_running "$DATA_DIR/doctor.pid"; then
  echo "系统自检正在运行，PID $(cat "$DATA_DIR/doctor.pid")"
else
  rm -f "$DATA_DIR/doctor.pid"
  pid="$(start_detached "$DATA_DIR/doctor.pid" "$DATA_DIR/doctor.log" \
    "$PYTHON_BIN" -u -m qis doctor)"
  echo "系统自检已启动，PID $pid"
fi

echo
echo "QIS 现货决策台已启动（只分析与手动登记，不会自动下单）"
echo "看盘地址: http://$WEB_HOST:$WEB_PORT/"
echo "查看状态: bash scripts/status.sh"
echo "停止系统: bash scripts/stop.sh"
