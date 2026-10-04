#!/bin/bash
# Investment Monitor — start from a terminal: ./start.sh  (macOS: double-click start-mac.command;
# Windows: start-windows.bat). The first start installs what it needs into .venv (about 2 minutes), then
# your browser opens http://localhost:8000. Ctrl-C stops. Arguments go to the server: ./start.sh --port 8001
cd "$(dirname "$0")" || exit 1
export PYTHONUTF8=1

# A Python 3.11+ that runs. Never macOS's /usr/bin/python3: it is 3.9, and without the developer tools it
# opens an install prompt instead of running.
usable() {
  local path
  path="$(command -v "$1" 2>/dev/null)" || return 1
  if [ "$(uname)" = Darwin ] && [ "$path" = /usr/bin/python3 ]; then return 1; fi
  "$path" -c 'import sys; sys.exit(sys.version_info < (3, 11))' >/dev/null 2>&1
}

# This folder's .venv when it works — a developer's own (or linked) one included: the bootstrap runs on it.
if [ -x .venv/bin/python ] && usable ./.venv/bin/python; then
  exec ./.venv/bin/python -m monitor.bootstrap "$@"
fi
# A git worktree without a .venv: the main checkout's, as it is.
if [ ! -e .venv ]; then
  MAIN="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"
  MAIN="${MAIN%/.git}"
  if [ -n "$MAIN" ] && [ "$MAIN" != "$PWD" ] && usable "$MAIN/.venv/bin/python"; then
    exec "$MAIN/.venv/bin/python" -m monitor serve "$@"
  fi
fi
# Otherwise the system's Python sets everything up (python.org's and Homebrew's folders are searched too).
PATH="$PATH:/usr/local/bin:/opt/homebrew/bin:/Library/Frameworks/Python.framework/Versions/Current/bin"
for PY in python3 python3.13 python3.12 python3.11 python3.14; do
  if usable "$PY"; then
    exec "$PY" -m monitor.bootstrap "$@"
  fi
done
echo "Investment Monitor needs Python 3.11 or newer, and none was found."
if [ "$(uname)" = Darwin ]; then
  echo "Get it from https://www.python.org/downloads/ (the macOS installer), then start again."
else
  echo "Install it with your package manager (e.g. sudo apt install python3 python3-venv) or from"
  echo "https://www.python.org/downloads/, then start again."
fi
exit 1
