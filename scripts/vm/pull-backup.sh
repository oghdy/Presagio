#!/usr/bin/env bash
# Fetch the newest collector VM dump to the Mac (P-044, D-058). Run on the Mac:
#
#   scripts/vm/pull-backup.sh <VM host> [ssh key]
#
# <VM host>: the VM's Tailscale address or public address (ssh only). ssh key: default
# ~/.ssh/presagio_vm. Copies the newest /var/backups/presagio/presagio-*.sql.gz into
# ~/Presagio-backups/ (PRESAGIO_BACKUP_LOCAL to change) and lists it. Restore: README section 8.
set -euo pipefail

HOST="${1:?usage: pull-backup.sh <VM host> [ssh key]}"
KEY="${2:-$HOME/.ssh/presagio_vm}"
LOCAL="${PRESAGIO_BACKUP_LOCAL:-$HOME/Presagio-backups}"
SSH_OPTS=(-i "$KEY" -o BatchMode=yes -o ConnectTimeout=10)

latest="$(ssh "${SSH_OPTS[@]}" "root@$HOST" 'ls -1t /var/backups/presagio/presagio-*.sql.gz 2> /dev/null | head -n 1')"
[ -n "$latest" ] || {
  echo "pull-backup.sh: no dump in /var/backups/presagio on $HOST" >&2
  exit 1
}
mkdir -p "$LOCAL"
scp -q "${SSH_OPTS[@]}" "root@$HOST:$latest" "$LOCAL/"
ls -l "$LOCAL/$(basename "$latest")"
