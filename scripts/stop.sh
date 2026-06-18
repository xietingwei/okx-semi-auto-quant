#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="$ROOT_DIR/data"

stop_process() {
  local name="$1"
  local pid_file="$2"
  if [[ ! -f "$pid_file" ]]; then
    echo "$name: 未启动"
    return
  fi
  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid"
    echo "$name: 已停止（PID ${pid}）"
  else
    echo "$name: 已经停止"
  fi
  rm -f "$pid_file"
}

stop_process "报告刷新" "$DATA_DIR/refresh.pid"
stop_process "系统自检" "$DATA_DIR/doctor.pid"
stop_process "paper 监控" "$DATA_DIR/qis-run.pid"
stop_process "网页服务" "$DATA_DIR/web.pid"
