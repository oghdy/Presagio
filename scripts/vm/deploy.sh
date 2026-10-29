#!/usr/bin/env bash
# Update the collector VM's code (P-044, D-058). From the Mac:
#
#   ssh -i ~/.ssh/presagio_vm root@<VM> 'bash -s' < scripts/vm/deploy.sh
#   ssh -i ~/.ssh/presagio_vm root@<VM> 'PRESAGIO_REF=<branch> bash -s' < scripts/vm/deploy.sh
#
# Runs as root on the VM after scripts/vm/setup.sh. Fast-forwards /opt/presagio to origin's
# PRESAGIO_REF (default main; refuses anything but a fast-forward), runs uv sync --frozen,
# copies changed systemd units and restarts the two timers. It does not touch .env, the database
# container or the schema: when alembic reports the database behind the code, it says so and the
# migration is applied on purpose (README section 6), not here. Packages, Docker and the firewall
# are setup.sh's job (run it again for those; it is idempotent). Run it outside the capture
# slots (KST 03:30-12:00 and 15:45-16:30): the timers restart and the venv is re-synced.
set -euo pipefail

REF="${PRESAGIO_REF:-main}"
APP="/opt/presagio"
UV="/usr/local/bin/uv"
UNITS=(presagio-odds.service presagio-odds.timer presagio-backup.service presagio-backup.timer)

say() { echo "[deploy] $*"; }

[ "$(id -u)" -eq 0 ] || {
  echo "[deploy] FAILED: run as root" >&2
  exit 1
}
[ -d "$APP/.git" ] || {
  echo "[deploy] FAILED: $APP is not set up (run scripts/vm/setup.sh first)" >&2
  exit 1
}
before="$(git -C "$APP" rev-parse --short HEAD)"
git -C "$APP" fetch --quiet origin "$REF"
git -C "$APP" checkout --quiet "$REF" 2> /dev/null \
  || git -C "$APP" checkout --quiet -b "$REF" --track "origin/$REF"
git -C "$APP" merge --quiet --ff-only "origin/$REF"
say "git: $REF $before -> $(git -C "$APP" log -1 --format='%h %s')"
(cd "$APP" && "$UV" sync --frozen --no-dev --quiet)
say "uv sync --frozen --no-dev: ok"

changed=0
for unit in "${UNITS[@]}"; do
  if ! cmp -s "$APP/scripts/vm/$unit" "/etc/systemd/system/$unit"; then
    install -m 644 "$APP/scripts/vm/$unit" "/etc/systemd/system/$unit"
    say "updated /etc/systemd/system/$unit"
    changed=1
  fi
done
[ "$changed" -eq 0 ] || systemctl daemon-reload
systemctl restart presagio-odds.timer presagio-backup.timer
# Errors are not hidden: with the database down this fails here, not as a false "behind".
current="$(cd "$APP" && "$UV" run --no-sync alembic current)"
say "alembic current: $current"
case "$current" in
  *"(head)"*) ;;
  *) say "WARNING: the database is behind the code's migrations; apply them on purpose" ;;
esac
systemctl list-timers --all --no-pager 'presagio-*' | sed 's/^/[deploy]   /'
