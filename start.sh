#!/bin/bash
# Investment Monitor terminal — the primary local view. Opens http://localhost:8000.
# Paints instantly from the stored payload, refreshes live in the background, and
# reloads by itself when code under monitor/ changes. Ctrl-C stops.
cd "$(dirname "$0")"
# Resolve the venv: local first, else the main checkout's (this may be a git worktree), else python3.
PY=.venv/bin/python
if [ ! -x "$PY" ]; then
  MAIN="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"
  MAIN="${MAIN%/.git}"
  [ -x "$MAIN/.venv/bin/python" ] && PY="$MAIN/.venv/bin/python"
fi
[ -x "$PY" ] || PY=python3
exec "$PY" -m monitor serve "$@"
