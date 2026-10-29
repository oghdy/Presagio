#!/usr/bin/env bash
# Daily dump of the collector VM database (P-044, D-058). presagio-backup.timer runs it at 12:00
# UTC as root; by hand on the VM:
#
#   bash /opt/presagio/scripts/vm/backup.sh
#
# Writes /var/backups/presagio/presagio-YYYYMMDD-HHMM.sql.gz (UTC time; pg_dump --clean
# --if-exists, so restoring it replaces the tables) and deletes dumps 14 days old or older
# (find -mtime +13: about 14 daily files are kept). The dump goes to a hidden partial file first
# and is renamed only after gzip -t and pg_dump's own completion line check out, so a failed run
# leaves no half dump and exits non-zero (journalctl -u presagio-backup). Restore: README
# section 8 (collector VM). Environment: PRESAGIO_APP, PRESAGIO_BACKUP_DIR (tests).
set -euo pipefail

APP="${PRESAGIO_APP:-/opt/presagio}"
DEST="${PRESAGIO_BACKUP_DIR:-/var/backups/presagio}"
KEEP_MTIME="+13"
name="presagio-$(date -u +%Y%m%d-%H%M).sql.gz"
partial="$DEST/.$name.partial"

install -d -m 0700 "$DEST"
trap 'rm -f "$partial"' EXIT
cd "$APP"
# The container's own POSTGRES_USER/POSTGRES_DB (from .env); local socket, no password needed.
# shellcheck disable=SC2016  # expanded inside the container
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" --clean --if-exists "$POSTGRES_DB"' \
  | gzip -9 > "$partial"
gzip -t "$partial"
tail_lines="$(gzip -dc "$partial" | tail -n 5)"
case "$tail_lines" in
  *"PostgreSQL database dump complete"*) ;;
  *)
    echo "backup.sh: dump has no completion line; not kept" >&2
    exit 1
    ;;
esac
chmod 600 "$partial"
mv "$partial" "$DEST/$name"
find "$DEST" -maxdepth 1 -type f -name 'presagio-*.sql.gz' -mtime "$KEEP_MTIME" -print -delete \
  | sed 's/^/backup.sh: deleted /'
echo "backup.sh: wrote $DEST/$name ($(stat -c %s "$DEST/$name") bytes)"
