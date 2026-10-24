#!/usr/bin/env bash
# SessionStart hook for Claude Code cloud sessions (P-040, P-042, D-052). Registered in
# .claude/settings.json; that file is shared by every session, local and cloud.
#
# Local session (CLAUDE_CODE_REMOTE unset): exits 0 at once, no output, touches nothing.
#
# Cloud session (CLAUDE_CODE_REMOTE=true, set by the platform): the environment snapshot keeps
# files but no processes, so PostgreSQL 16 is down at every session start. This script starts it
# if needed, checks the DATABASE_URL connection once with the project's own checker
# (python -m presagio.db.session), and then asks alembic whether the snapshot's database is at
# the repository's migration head: the snapshot was taken when the setup script ran, so a
# migration merged later leaves the database behind until scripts/cloud-setup.sh runs again.
#
# The result is exactly ONE line on stdout, whatever happened: Claude Code adds a SessionStart
# hook's plain stdout to Claude's context, so the agent knows whether the database is usable
# before its first command. Command output that spans lines (a traceback, uv progress) is
# collapsed into that line. The exit code is always 0 so a session can still start and report
# the problem; a failure line is also written to stderr for the human.
#
# If DATABASE_URL is missing from the environment, the cloud default from scripts/cloud/db.conf
# is exported for this session through CLAUDE_ENV_FILE and the line says so.
#
# Claude Code sets CLAUDE_PROJECT_DIR (the repository root) for hook commands; the session shell
# does not have it (measured 2026-10-24), so when run by hand the script finds the repository
# from its own path.
set -uo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/cloud/db.conf
. "$HERE/db.conf"
ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$HERE/../.." && pwd)}"
TAG="[presagio cloud]"
FIX="run: bash scripts/cloud-setup.sh"
PG_WAIT_SECONDS=30
export PATH="${PATH}:${HOME:-/root}/.local/bin"

say() { echo "$TAG $*"; }
complain() { echo "$TAG $*"; echo "$TAG $*" >&2; }
# Collapse multi-line command output into one line (newlines and runs of blanks become one
# space) so the hook keeps its one-line contract.
one_line() { printf '%s' "$*" | tr '\r\n\t' '   ' | sed -E 's/ +/ /g; s/^ //; s/ $//'; }
# Run `python -m <module> [args]` inside the project: .venv is preferred; if the snapshot did
# not keep it, uv run syncs it first. Callers choose what to capture (stdout, or stdout+stderr).
in_project() {
  if [ -x "$ROOT/.venv/bin/python" ]; then
    (cd "$ROOT" && exec "$ROOT/.venv/bin/python" -m "$@")
  elif command -v uv > /dev/null 2>&1; then
    (cd "$ROOT" && exec uv run --project "$ROOT" --locked --quiet python -m "$@")
  else
    echo "neither $ROOT/.venv nor uv found; ${FIX}"
    return 1
  fi
}

# 1. PostgreSQL up?
if ! pg_isready -q -h "$CLOUD_DB_HOST" -p "$CLOUD_DB_PORT" 2> /dev/null; then
  if ! service postgresql start > /dev/null 2>&1; then
    complain "could not start PostgreSQL (service postgresql start failed); ${FIX}"
    exit 0
  fi
  for _ in $(seq 1 "$PG_WAIT_SECONDS"); do
    if pg_isready -q -h "$CLOUD_DB_HOST" -p "$CLOUD_DB_PORT" 2> /dev/null; then break; fi
    sleep 1
  done
  if ! pg_isready -q -h "$CLOUD_DB_HOST" -p "$CLOUD_DB_PORT" 2> /dev/null; then
    complain "PostgreSQL started but is not accepting connections on ${CLOUD_DB_HOST}:${CLOUD_DB_PORT} after ${PG_WAIT_SECONDS}s"
    exit 0
  fi
  started="started PostgreSQL"
else
  started="PostgreSQL already running"
fi

# 2. DATABASE_URL present? Fall back to the cloud default and persist it for the session.
url_note=""
if [ -z "${DATABASE_URL:-}" ]; then
  export DATABASE_URL="$CLOUD_DATABASE_URL"
  if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
    printf 'export DATABASE_URL=%q\n' "$CLOUD_DATABASE_URL" >> "$CLAUDE_ENV_FILE"
  fi
  url_note=" DATABASE_URL was not set in the environment, so the cloud default from scripts/cloud/db.conf is used for this session (add it to the environment variables, README \"클라우드 세션\")."
fi
if [ "${PRESAGIO_REQUIRE_DB:-}" = "1" ]; then
  require_note="PRESAGIO_REQUIRE_DB=1 (DB tests fail instead of skip)."
else
  require_note="PRESAGIO_REQUIRE_DB is not 1: a broken DB would make DB tests skip silently; set it in the environment."
fi

# 3. One connection check with the application's own code path (psycopg, same URL the tests use).
#    stderr is kept: on failure it holds the reason ("FAILED: OperationalError: ...").
check_out="$(in_project presagio.db.session 2>&1)"
check_rc=$?
check_out="$(one_line "$check_out")"

# 4. Snapshot database at the repository's migration head? `alembic current` prints
#    "<revision> (head)" only when the database revision is the newest one in alembic/versions;
#    "<revision>" alone means behind, nothing means no alembic_version table. stdout carries the
#    revision; stderr (alembic's INFO logging, and the traceback when it fails) goes to a temp
#    file so a failure's reason can be kept. Skipped when the connection check already failed.
migration_note=""
if [ "$check_rc" -eq 0 ]; then
  alembic_err="$(mktemp)"
  alembic_out="$(in_project alembic current 2> "$alembic_err")"
  alembic_rc=$?
  alembic_out="$(one_line "$alembic_out")"
  # The last informative stderr line: not alembic's logging, not SQLAlchemy's "(Background on
  # this error at: ...)" link that ends every one of its errors.
  alembic_reason="$(grep -v -e '^INFO ' -e '^(Background on this error' "$alembic_err" | tail -n 1)"
  rm -f "$alembic_err"
  if [ "$alembic_rc" -ne 0 ]; then
    alembic_reason="$(one_line "${alembic_reason:-no message on stderr}")"
    migration_note=" alembic current failed (exit ${alembic_rc}: ${alembic_reason}); ${FIX}"
  else
    case "$alembic_out" in
      *"(head)"*) ;;
      *) migration_note=" snapshot DB behind repo migrations (alembic current: ${alembic_out:-<none>}); ${FIX}" ;;
    esac
  fi
fi

if [ "$check_rc" -eq 0 ]; then
  say "DB ready (${started}). ${check_out}. ${require_note}${url_note}${migration_note}"
else
  complain "DB check failed (${started}): ${check_out}. ${require_note}${url_note} Fix: bash scripts/cloud-setup.sh"
fi
exit 0
