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
# The Mac side is read-only: pg_dump inside the Mac container and SELECTs. docker compose reads
# the checkout's .env (if any) for variable interpolation only; nothing from it is printed or
# sent to the VM. The VM side is overwritten: the dump has --clean --if-exists, so every table
# on the VM is replaced by the Mac's, in one transaction (psql -1: a stream cut halfway leaves
# the VM database as it was). Because that would also erase odds the VM captured on its own,
# the script refuses when any distinct odds_snapshots.captured_at on the VM (one per capture
# call) is missing on the Mac - it prints how many and the earliest and latest - unless --force
# is given. Runs without a capture (the VM's timer before ODDS_API_KEY is set: odds step
# 'skipped') add no snapshot, so they do not count; they are replaced. Avoid the capture slots
# (README section 8). At the end it prints the row counts of seven tables on both sides and
# exits 1 if any differ.
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

# lines TEXT: number of non-empty lines.
lines() { printf '%s\n' "$1" | grep -c . || true; }

counts_sql=""
for t in "${TABLES[@]}"; do
  counts_sql+="${counts_sql:+ UNION ALL }SELECT '$t', count(*) FROM $t"
done

# One line per capture call: microseconds since the epoch (exact match) | UTC time (to print).
captures="SELECT DISTINCT (extract(epoch FROM captured_at) * 1000000)::bigint || '|' || to_char(captured_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') FROM odds_snapshots"
mac_captures="$(mac_sql "$captures")"
vm_captures="$(vm_sql "$captures")"
vm_only="$(awk -F'|' 'NR == FNR { mac[$1] = 1; next } $1 != "" && !($1 in mac)' \
  <(printf '%s\n' "$mac_captures") <(printf '%s\n' "$vm_captures") | sort -t'|' -k1,1n)"
echo "capture times (distinct odds_snapshots.captured_at): Mac $(lines "$mac_captures"), VM $(lines "$vm_captures"), VM only $(lines "$vm_only")"
if [ -n "$vm_only" ]; then
  first="$(printf '%s\n' "$vm_only" | head -n 1 | cut -d'|' -f2)"
  last="$(printf '%s\n' "$vm_only" | tail -n 1 | cut -d'|' -f2)"
  if [ "$FORCE" -eq 0 ]; then
    echo "db-copy-from-mac.sh: the VM has $(lines "$vm_only") capture time(s) the Mac does not" >&2
    echo "  (earliest $first UTC, latest $last UTC); copying would erase them. Back up first" >&2
    echo "  (scripts/vm/pull-backup.sh) and pass --force." >&2
    exit 1
  fi
  echo "--force: overwriting the VM's own captures ($first .. $last UTC)"
fi

started=$SECONDS
mac_compose exec -T postgres pg_dump -U presagio --clean --if-exists presagio \
  | vm "cd /opt/presagio && docker compose exec -T postgres psql -U presagio -d presagio -q -1 -v ON_ERROR_STOP=1 > /dev/null"
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
