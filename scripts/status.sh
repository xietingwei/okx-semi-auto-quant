#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="$ROOT_DIR/data"
WEB_HOST="${QIS_WEB_HOST:-127.0.0.1}"
WEB_PORT="${QIS_WEB_PORT:-8787}"

show_process() {
  local name="$1"
  local pid_file="$2"
  if [[ ! -f "$pid_file" ]]; then
    echo "$name: 未启动"
    return
  fi
  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    echo "$name: 运行中（PID ${pid}）"
  else
    echo "$name: 已完成或停止（PID ${pid:-未知}）"
  fi
}

show_process "现货预测" "$DATA_DIR/spot-watch.pid"
show_process "网页服务" "$DATA_DIR/web.pid"
show_process "系统自检" "$DATA_DIR/doctor.pid"

if curl -fsS --max-time 2 "http://$WEB_HOST:$WEB_PORT/" >/dev/null 2>&1; then
  echo "看盘 URL: http://$WEB_HOST:$WEB_PORT/"
else
  echo "看盘 URL: 当前不可访问"
fi

echo
echo "现货预测日志:"
tail -n 8 "$DATA_DIR/spot-watch.log" 2>/dev/null || true
echo
echo "自检日志:"
tail -n 8 "$DATA_DIR/doctor.log" 2>/dev/null || true
