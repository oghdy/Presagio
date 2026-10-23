#!/usr/bin/env bash
# Remove the Presagio launchd job (P-024). Logs and slot markers are kept.
#
#   scripts/launchd/uninstall.sh
set -euo pipefail

LABEL="com.presagio.ingest-daily"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if launchctl print "gui/$(id -u)/$LABEL" > /dev/null 2>&1; then
  launchctl bootout "gui/$(id -u)/$LABEL"
else
  echo "($LABEL was not loaded)"
fi
rm -f "$PLIST"
echo "Removed $PLIST"
echo "launchctl list | grep presagio  (expected: nothing)"
launchctl list | grep presagio || true
