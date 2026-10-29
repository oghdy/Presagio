#!/usr/bin/env bash
# Copy the Mac's development database to the collector VM (P-044, D-058). Run on the Mac, from
# a Presagio checkout:
#
#   scripts/vm/db-copy-from-mac.sh <VM host> [ssh key] [--force]
#
# <VM host>: the VM's Tailscale address (100.x) or its public address; only ssh (22) is used,
# the database port is never reached from outside. ssh key: default ~/.ssh/presagio_vm.
# Prerequisites: the Mac's Docker Desktop is running with the `presagio` compose project up, and
# the VM is set up (scripts/vm/setup.sh).
#
# The Mac side is read-only: pg_dump inside the Mac container and row counts; no .env is read.
# The VM side is overwritten: the dump has --clean --if-exists, so every table on the VM is
# replaced by the Mac's. Because that would also erase closing captures the VM made on its own,
# the script refuses when the VM has ingestion_runs ids above the Mac's highest one (runs only
# the VM has) unless --force is given. Avoid the capture slots (README section 8). At the end it
# prints the row counts of seven tables on both sides and exits 1 if any differ.
set -euo pipefail

TABLES=(odds_snapshots ingestion_runs games team_game_stats predictions evaluations model_versions)
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HOST="${1:?usage: db-copy-from-mac.sh <VM host> [ssh key] [--force]}"
KEY="${HOME}/.ssh/presagio_vm"
FORCE=0
for arg in "${@:2}"; do
  case "$arg" in
    --force) FORCE=1 ;;
    *) KEY="$arg" ;;
  esac
done

mac_compose() { docker compose -p presagio -f "$REPO/docker-compose.yml" "$@"; }
vm() { ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=10 "root@$HOST" "$@"; }
# psql with -At on each side; the VM container is reached through its compose project.
mac_sql() { mac_compose exec -T postgres psql -U presagio -d presagio -At -v ON_ERROR_STOP=1 -c "$1"; }
vm_sql() { vm "cd /opt/presagio && docker compose exec -T postgres psql -U presagio -d presagio -At -v ON_ERROR_STOP=1 -c \"$1\""; }

counts_sql=""
for t in "${TABLES[@]}"; do
  counts_sql+="${counts_sql:+ UNION ALL }SELECT '$t', count(*) FROM $t"
done

mac_max="$(mac_sql "SELECT coalesce(max(run_id), 0) FROM ingestion_runs")"
vm_max="$(vm_sql "SELECT coalesce(max(run_id), 0) FROM ingestion_runs" 2> /dev/null || echo 0)"
echo "ingestion_runs max(run_id): Mac $mac_max, VM $vm_max"
if [ "${vm_max:-0}" -gt "$mac_max" ] && [ "$FORCE" -eq 0 ]; then
  echo "db-copy-from-mac.sh: the VM has runs the Mac does not (run_id > $mac_max); copying" >&2
  echo "  would erase them. Back up first (scripts/vm/pull-backup.sh) and pass --force." >&2
  exit 1
fi

started=$SECONDS
mac_compose exec -T postgres pg_dump -U presagio --clean --if-exists presagio \
  | vm "cd /opt/presagio && docker compose exec -T postgres psql -U presagio -d presagio -q -v ON_ERROR_STOP=1 > /dev/null"
echo "restored in $((SECONDS - started)) s"

mac_counts="$(mac_sql "$counts_sql")"
vm_counts="$(vm_sql "$counts_sql")"
status=0
printf '%-16s %10s %10s  %s\n' table mac vm same
for t in "${TABLES[@]}"; do
  m="$(echo "$mac_counts" | awk -F'|' -v t="$t" '$1 == t {print $2}')"
  v="$(echo "$vm_counts" | awk -F'|' -v t="$t" '$1 == t {print $2}')"
  same=yes
  [ "$m" = "$v" ] || {
    same=NO
    status=1
  }
  printf '%-16s %10s %10s  %s\n' "$t" "$m" "$v" "$same"
done
echo "alembic_version: Mac $(mac_sql 'SELECT version_num FROM alembic_version'), VM $(vm_sql 'SELECT version_num FROM alembic_version')"
exit "$status"
