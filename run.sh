#!/usr/bin/env bash
set -e

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
export PYTHONPATH="$ROOT_DIR/src"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-7860}"
LOG_DIR="$ROOT_DIR/logs"

mkdir -p "$LOG_DIR"

python -m open_storyline.mcp.server >> "$LOG_DIR/mcp.log" 2>&1 &
MCP_PID=$!

uvicorn agent_fastapi:app \
  --host "$HOST" \
  --port "$PORT" >> "$LOG_DIR/server.log" 2>&1 &
WEB_PID=$!

echo "MCP  server started (PID=$MCP_PID) → $LOG_DIR/mcp.log"
echo "Web  server started (PID=$WEB_PID) → $LOG_DIR/server.log"

trap 'kill $MCP_PID $WEB_PID' INT TERM

wait
