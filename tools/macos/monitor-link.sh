#!/bin/bash
# What the monitor:// link does (tools/macos/install-link.sh installs the app that runs this):
#   monitor-link.sh open   start the terminal in the background unless it already answers, then open it
#   monitor-link.sh stop   stop the terminal serving the port — only a monitor server, nothing else on it
# Only these two words are accepted: the link's own text never reaches this script or any command.
# The server's output goes to local/server.log. MONITOR_PORT picks another port (default 8000).
set -u
cd "$(dirname "$0")/../.." || exit 1
PORT="${MONITOR_PORT:-8000}"
URL="http://localhost:$PORT"
LOG="local/server.log"
OPENER="${MONITOR_LINK_OPENER:-/usr/bin/open}"          # the tests swap the browser for a stub

answers() { curl -fs -o /dev/null -m 2 "$URL/api/screens"; }

# The monitor servers listening on the port (uvicorn's reloader and its worker both hold the socket;
# stopping the `monitor serve` parent stops the worker).
servers() {
  local pid
  for pid in $(lsof -nP -t -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null); do
    if ps -o command= -p "$pid" | grep -q -- "-m monitor serve"; then echo "$pid"; fi
  done
}

open_it() {
  if ! answers; then
    mkdir -p local
    echo "── $(date '+%Y-%m-%d %H:%M:%S') started by monitor://open" >> "$LOG"
    nohup ./start.sh --no-open --port "$PORT" >> "$LOG" 2>&1 < /dev/null &
    for _ in $(seq 1 300); do answers && break; sleep 0.5; done     # a first start installs: ~2 min
    if ! answers; then
      osascript -e "display alert \"Investment Monitor did not start\" message \"See $PWD/$LOG\"" >/dev/null 2>&1
      exit 1
    fi
  fi
  "$OPENER" "$URL"
}

stop_it() {
  local pids
  pids="$(servers)"
  [ -n "$pids" ] || exit 0
  kill $pids 2>/dev/null
  for _ in $(seq 1 40); do [ -z "$(servers)" ] && exit 0; sleep 0.25; done
  pids="$(servers)"
  [ -n "$pids" ] && kill -9 $pids 2>/dev/null
  exit 0
}

case "${1:-}" in
  open) open_it ;;
  stop) stop_it ;;
  *) echo "usage: $0 open|stop" >&2; exit 2 ;;
esac
