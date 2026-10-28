#!/usr/bin/env bash
# Install the Presagio interim schedule as a user LaunchAgent (P-024, D-048).
#
#   scripts/launchd/install.sh [REPO_PATH]
#
# REPO_PATH is where ingest-daily runs: the checkout whose root holds .env (DATABASE_URL,
# ODDS_API_KEY). Default: the repository this script is in. The plist uses REPO_PATH's own
# scripts/launchd files (never this script's checkout), and the script fills the uv path, the
# repository path and the log/state folders into it. The wake-up times come from REPO_PATH/.env,
# read by presagio.config; nothing from .env is copied or printed.
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
WRAPPER="$REPO/scripts/launchd/presagio_launchd.py"
TEMPLATE="$REPO/scripts/launchd/$LABEL.plist.template"
[ -f "$WRAPPER" ] && [ -f "$TEMPLATE" ] \
  || die "$REPO has no scripts/launchd (check out a version with P-024 there first)"
# StartCalendarInterval is local time and the wake-up list assumes Korea (no daylight saving).
case "$(readlink /etc/localtime)" in
  */Asia/Seoul) ;;
  *) die "the Mac's time zone is not Asia/Seoul; the wake-up times assume it" ;;
esac

mkdir -p "$LOG_DIR" "$STATE_DIR" "$AGENTS"
TMP="$(mktemp -t presagio-plist)"
trap 'rm -f "$TMP"' EXIT
# Run from the repository so presagio.config reads the same .env (the times) as the runs will.
(cd "$REPO" && "$UV" run --project "$REPO" python "$WRAPPER" render \
  --template "$TEMPLATE" --repo "$REPO" --uv "$UV" --wrapper "$WRAPPER" \
  --log-dir "$LOG_DIR" --state-dir "$STATE_DIR") > "$TMP"
plutil -lint "$TMP" > /dev/null

# Replace any earlier copy of the job, then load the new one.
launchctl bootout "$DOMAIN/$LABEL" 2> /dev/null || true
cp "$TMP" "$PLIST"
chmod 644 "$PLIST"
launchctl bootstrap "$DOMAIN" "$PLIST"

echo "Installed $PLIST"
echo "  runs in:  $REPO"
echo "  wrapper:  $WRAPPER"
echo "  uv:       $UV"
echo "  logs:     $LOG_DIR/ingest-<KST date>.log"
echo "  wake-ups (KST):"
(cd "$REPO" && "$UV" run --project "$REPO" python "$WRAPPER" calendar) | sed 's/^/    /'
echo
echo "The job has just run once (RunAtLoad; at login too, if launchd loads it then: see"
echo "  man launchd.plist, not yet measured on this Mac): it skips unless now is a"
echo "  slot, or today's full run is due (after INGEST_DAILY_TIME_KST) and has not run yet;"
echo "  then that full run starts now. See $LOG_DIR/ingest-$(date +%F).log"
case "$REPO" in
  "$HOME"/Desktop/* | "$HOME"/Documents/* | "$HOME"/Downloads/*)
    echo
    echo "NOTE: $REPO is in a privacy-protected folder. That first run asks for permission"
    echo "  (\"uv\" wants to access the Desktop/Documents/Downloads folder) and waits until"
    echo "  someone clicks Allow: answer the prompt now. If none appeared and today's log has"
    echo "  no line from just now, wake the job again:"
    echo "    launchctl kickstart gui/$(id -u)/$LABEL"
    ;;
esac
echo
echo "launchctl list | grep presagio"
launchctl list | grep presagio
