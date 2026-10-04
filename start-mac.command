#!/bin/bash
# Investment Monitor for macOS — double-click this file in Finder. The first start installs what it needs
# (about 2 minutes), then your browser opens http://localhost:8000. Keep this window open while you use
# it; close it (or press Ctrl-C) to stop.
cd "$(dirname "$0")" || exit 1
if [ ! -f monitor/bootstrap.py ]; then
  echo "The monitor folder is missing next to this file — unzip the whole download, then start again."
  status=1
else
  bash ./start.sh "$@"
  status=$?
fi
if [ "$status" -ne 0 ] && [ "$status" -ne 130 ]; then
  echo
  read -r -p "Something went wrong (see above). Press Enter to close this window. " _
fi
exit "$status"
