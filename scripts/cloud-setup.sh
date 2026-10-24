#!/usr/bin/env bash
# Set up a Claude Code cloud session VM for Presagio (P-040, P-042, D-052).
#
# The "설정 스크립트" (setup script) field of the claude.ai/code environment holds the WRAPPER
# printed in README "운영: Claude Code 클라우드 세션", not a call to this file by relative path:
# the platform runs the setup script from a directory that is not the repository root (measured
# 2026-10-23: `bash scripts/cloud-setup.sh` there ended in "No such file or directory", exit 127,
# and no session started; the official docs do not name the directory). The wrapper locates this
# file by absolute path (CLAUDE_PROJECT_DIR when set, else find) and runs it. Inside a session
# the shell starts at the repository root, so `bash scripts/cloud-setup.sh` by hand is fine; the
# script finds the repository from its own location either way.
#
# The platform runs it as root on the Ubuntu 24.04 session VM before Claude Code launches, and
# snapshots the filesystem when it finishes within roughly five minutes; later sessions start
# from that snapshot (about seven days, then it runs again). Only files survive in the snapshot,
# not processes, so PostgreSQL is started again per session by scripts/cloud/session-start.sh.
#
# Steps (each re-runnable; running the script twice ends in the same state):
#   1. uv        use the pre-installed uv, else install it from PyPI (astral.sh is not reachable
#                from the default Trusted network; PyPI is).
#   2. uv sync   --locked, into .venv. uv downloads CPython 3.12 itself (python-preference is
#                only-managed; GitHub release downloads are reachable).
#   3. postgres  service postgresql start (PostgreSQL 16 is pre-installed but stopped).
#   4. role/db   role presagio with LOGIN CREATEDB (tests create temporary databases, D-030)
#                and database presagio. Values come from scripts/cloud/db.conf; the password is
#                cloud-only and not a secret.
#   5. alembic   uv run alembic upgrade head against that database.
#
# Exit code 0 only when every step succeeded. On failure the last line on stderr is
# "cloud-setup.sh: FAILED at <step>: <reason>" and the exit code is non-zero (the platform then
# refuses to start the session, which is the honest outcome: no silent half-setup).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
# shellcheck source=scripts/cloud/db.conf
. "$HERE/cloud/db.conf"

START=$(date +%s)
STEP="start"
PG_WAIT_SECONDS=30

elapsed() { echo "$(( $(date +%s) - START ))s"; }
fail() { echo "cloud-setup.sh: FAILED at ${STEP}: $*" >&2; exit 1; }
step() { STEP="$1"; echo "==> [$(elapsed)] ${STEP}"; }
# Any unhandled non-zero command ends the run with the step name; `fail` gives a better reason
# where one is known.
trap 'rc=$?; trap - ERR; fail "command exited with $rc"' ERR

step "uv"
# uv lives in ~/.local/bin when installed by its own installer; appended (not prepended) so an
# explicit earlier PATH entry still wins.
export PATH="${PATH}:${HOME:-/root}/.local/bin"
if ! command -v uv > /dev/null 2>&1; then
  echo "uv not found; installing from PyPI (astral.sh is not in the Trusted allowlist)"
  python3 -m pip install --quiet --break-system-packages --root-user-action=ignore uv \
    || fail "could not install uv from PyPI (python3 -m pip install uv)"
  command -v uv > /dev/null 2>&1 || fail "uv is still not on PATH after pip install"
fi
echo "uv: $(uv --version)"

step "uv sync --locked"
cd "$REPO"
# --locked: fail if uv.lock is stale instead of silently re-resolving (same as CI).
uv sync --locked || fail "uv sync --locked failed (stale uv.lock, or PyPI/GitHub unreachable)"
echo "python: $(.venv/bin/python --version)"

step "postgresql start"
command -v pg_isready > /dev/null 2>&1 || fail "PostgreSQL client tools are missing (pg_isready)"
service postgresql start || fail "service postgresql start failed"
for _ in $(seq 1 "$PG_WAIT_SECONDS"); do
  if pg_isready -q -h "$CLOUD_DB_HOST" -p "$CLOUD_DB_PORT"; then break; fi
  sleep 1
done
pg_isready -q -h "$CLOUD_DB_HOST" -p "$CLOUD_DB_PORT" \
  || fail "PostgreSQL did not accept connections on ${CLOUD_DB_HOST}:${CLOUD_DB_PORT} within ${PG_WAIT_SECONDS}s"
echo "postgres: $(runuser -u postgres -- psql -Atqc 'SHOW server_version')"

step "role and database"
# Runs as the postgres OS user (peer auth). CREATE only when missing; ALTER every time so a
# re-run also repairs the password or a dropped CREATEDB. \gexec runs each returned row as SQL.
runuser -u postgres -- psql -v ON_ERROR_STOP=1 -Atq -d postgres \
  -v role="$CLOUD_DB_USER" -v pw="$CLOUD_DB_PASSWORD" -v db="$CLOUD_DB_NAME" <<'SQL' \
  || fail "could not create or update the role/database as the postgres OS user"
SELECT format('CREATE ROLE %I LOGIN CREATEDB PASSWORD %L', :'role', :'pw')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'role') \gexec
SELECT format('ALTER ROLE %I LOGIN CREATEDB PASSWORD %L', :'role', :'pw') \gexec
SELECT format('CREATE DATABASE %I OWNER %I', :'db', :'role')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'db') \gexec
SQL
# Prove the TCP + password path the application will use, and the CREATEDB the tests need.
role_check="$(PGPASSWORD="$CLOUD_DB_PASSWORD" PGCONNECT_TIMEOUT=5 psql -h "$CLOUD_DB_HOST" \
  -p "$CLOUD_DB_PORT" -U "$CLOUD_DB_USER" -d "$CLOUD_DB_NAME" -Atqc \
  "SELECT current_user || ' createdb=' || rolcreatedb::text FROM pg_roles WHERE rolname = current_user")" \
  || fail "role ${CLOUD_DB_USER} cannot log in over TCP with the cloud password"
[ "$role_check" = "${CLOUD_DB_USER} createdb=true" ] || fail "unexpected role state: ${role_check}"
echo "role/db: ${role_check}, database ${CLOUD_DB_NAME}"

step "alembic upgrade head"
if [ -n "${DATABASE_URL:-}" ] && [ "$DATABASE_URL" != "$CLOUD_DATABASE_URL" ]; then
  echo "note: DATABASE_URL in the environment differs from scripts/cloud/db.conf;" \
    "migrating the cloud database anyway. Tests use the environment's value."
fi
DATABASE_URL="$CLOUD_DATABASE_URL" uv run --locked alembic upgrade head \
  || fail "alembic upgrade head failed"
current="$(DATABASE_URL="$CLOUD_DATABASE_URL" uv run --locked alembic current 2> /dev/null)"
echo "alembic: ${current:-<no revision>}"

trap - ERR
echo "cloud-setup.sh: OK in $(elapsed) (database ${CLOUD_DATABASE_URL%%:*}://${CLOUD_DB_USER}:***@${CLOUD_DB_HOST}:${CLOUD_DB_PORT}/${CLOUD_DB_NAME})"
