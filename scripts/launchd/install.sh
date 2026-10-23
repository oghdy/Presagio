#!/usr/bin/env bash
# Install the Presagio interim schedule as a user LaunchAgent (P-024, D-048).
#
#   scripts/launchd/install.sh [REPO_PATH]
#
# REPO_PATH is where ingest-daily runs: the checkout whose root holds .env (DATABASE_URL,
# ODDS_API_KEY). Default: the repository this script is in. The script fills the uv path, the
# repository path, this wrapper's path and the log/state folders into the plist; .env is only
# checked for existence, never read, copied or printed here.
set -euo pipefail

LABEL="com.presagio.ingest-daily"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${1:-$(git -C "$HERE" rev-parse --show-toplevel)}"
REPO="$(cd "$REPO" && pwd)"
LOG_DIR="${PRESAGIO_LAUNCHD_LOG_DIR:-$HOME/Library/Logs/Presagio}"
STATE_DIR="${PRESAGIO_LAUNCHD_STATE_DIR:-$HOME/Library/Application Support/Presagio/launchd}"
AGENTS="$HOME/Library/LaunchAgents"
PLIST="$AGENTS/$LABEL.plist"
DOMAIN="gui/$(id -u)"

die() { echo "install.sh: $*" >&2; exit 1; }

[ "$(uname)" = "Darwin" ] || die "launchd is macOS only"
UV="$(command -v uv)" || die "uv not found on PATH (README section 1)"
[ -f "$REPO/pyproject.toml" ] || die "$REPO is not the Presagio repository"
[ -f "$REPO/.env" ] || die "$REPO/.env is missing (cp .env.example .env, README section 5)"
# StartCalendarInterval is local time and the wake-up list assumes Korea (no daylight saving).
case "$(readlink /etc/localtime)" in
  */Asia/Seoul) ;;
  *) die "the Mac's time zone is not Asia/Seoul; the wake-up times assume it" ;;
esac

mkdir -p "$LOG_DIR" "$STATE_DIR" "$AGENTS"
TMP="$(mktemp -t presagio-plist)"
trap 'rm -f "$TMP"' EXIT
# Run from the repository so presagio.config reads the same .env (the times) as the runs will.
(cd "$REPO" && "$UV" run --project "$REPO" python "$HERE/presagio_launchd.py" render \
  --template "$HERE/$LABEL.plist.template" --repo "$REPO" --uv "$UV" \
  --log-dir "$LOG_DIR" --state-dir "$STATE_DIR") > "$TMP"
plutil -lint "$TMP" > /dev/null

# Replace any earlier copy of the job, then load the new one.
launchctl bootout "$DOMAIN/$LABEL" 2> /dev/null || true
cp "$TMP" "$PLIST"
chmod 644 "$PLIST"
launchctl bootstrap "$DOMAIN" "$PLIST"

echo "Installed $PLIST"
echo "  runs in:  $REPO"
echo "  uv:       $UV"
echo "  logs:     $LOG_DIR/ingest-<KST date>.log"
echo "  wake-ups (KST):"
(cd "$REPO" && "$UV" run --project "$REPO" python "$HERE/presagio_launchd.py" calendar) | sed 's/^/    /'
case "$REPO" in
  "$HOME"/Desktop/* | "$HOME"/Documents/* | "$HOME"/Downloads/*)
    echo
    echo "NOTE: $REPO is in a privacy-protected folder. The first run asks for permission"
    echo "  (\"uv\" wants to access the Desktop/Documents/Downloads folder) and waits until"
    echo "  someone clicks Allow. Wake the job now and answer the prompt:"
    echo "    launchctl kickstart gui/$(id -u)/$LABEL"
    ;;
esac
echo
echo "launchctl list | grep presagio"
launchctl list | grep presagio
