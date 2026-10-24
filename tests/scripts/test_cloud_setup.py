"""scripts/cloud-setup.sh, scripts/cloud/session-start.sh and .claude/settings.json (P-040, P-042).

No network and no database: the cloud-only commands (pg_isready, service, uv, runuser, psql, the
venv python) are replaced by stubs on PATH or in a temporary project directory. Every stub appends
one line per call to a log, so the tests can assert the order and the exact arguments.
"""

from __future__ import annotations

import json
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SETUP = REPO_ROOT / "scripts" / "cloud-setup.sh"
HOOK = REPO_ROOT / "scripts" / "cloud" / "session-start.sh"
CONF = REPO_ROOT / "scripts" / "cloud" / "db.conf"
SETTINGS = REPO_ROOT / ".claude" / "settings.json"
README = REPO_ROOT / "README.md"
BASH = shutil.which("bash") or "/bin/bash"
# Real coreutils (dirname, seq, tr, sed ...) but no real pg_isready/service/uv: the stub dir goes
# first.
SYSTEM_PATH = "/usr/bin:/bin"
TAG = "[presagio cloud]"
BEHIND = "snapshot DB behind repo migrations"
FIX = "run: bash scripts/cloud-setup.sh"


def conf_value(name: str) -> str:
    """Read one variable the way the scripts do: by sourcing db.conf."""
    result = subprocess.run(
        [BASH, "-c", f'. "$1" && printf %s "${name}"', "_", str(CONF)],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


CLOUD_URL = conf_value("CLOUD_DATABASE_URL")
CLOUD_PASSWORD = conf_value("CLOUD_DB_PASSWORD")


def write_stub(directory: Path, name: str, body: str, **placeholders: Path) -> Path:
    """Write an executable bash stub called `name`; `__KEY__` in the body becomes the path."""
    for key, value in placeholders.items():
        body = body.replace(f"__{key}__", str(value))
    path = directory / name
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def run(script: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run a script with bash, exactly the given environment and no stdin."""
    return subprocess.run(
        [BASH, str(script)],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


# --- fake cloud VM for the hook -------------------------------------------------------------


class CloudStubs:
    """A fake cloud VM: stub commands on PATH and a fake project with a fake .venv python."""

    def __init__(self, tmp_path: Path) -> None:
        self.bin = tmp_path / "bin"
        self.project = tmp_path / "project"
        self.log = tmp_path / "calls.log"
        self.env_file = tmp_path / "claude.env"
        self.pg_up_marker = tmp_path / "pg-up"
        self.db_fail_marker = tmp_path / "db-fail"
        self.alembic_fail_marker = tmp_path / "alembic-fail"
        self.alembic_out = tmp_path / "alembic-current.txt"
        self.bin.mkdir()
        (self.project / ".venv" / "bin").mkdir(parents=True)
        self.env_file.touch()
        self.alembic_out.write_text("0002 (head)\n")
        # pg_isready is "ready" only while the marker exists; the `service` stub recreates it.
        write_stub(
            self.bin,
            "pg_isready",
            'echo "pg_isready $*" >> __LOG__\n[ -e __PG_UP__ ]\n',
            LOG=self.log,
            PG_UP=self.pg_up_marker,
        )
        write_stub(
            self.bin,
            "service",
            'echo "service $*" >> __LOG__\n'
            '[ "$1 $2" = "postgresql start" ] && touch __PG_UP__ && exit 0\nexit 1\n',
            LOG=self.log,
            PG_UP=self.pg_up_marker,
        )
        # The fake venv python answers `-m presagio.db.session` (the connection check; a failure
        # is three lines on stderr like the real checker) and `-m alembic current` (which, like
        # the real alembic, logs to stderr and prints the revision on stdout).
        write_stub(
            self.project / ".venv" / "bin",
            "python",
            'echo "python $* DATABASE_URL=$DATABASE_URL" >> __LOG__\n'
            'if [ "${2:-}" = alembic ]; then\n'
            '  echo "INFO  [alembic.runtime.migration] Context impl PostgresqlImpl." >&2\n'
            "  if [ -e __ALEMBIC_FAIL__ ]; then\n"
            '    echo "Traceback (most recent call last):" >&2\n'
            '    echo "sqlalchemy.exc.ProgrammingError: stub: relation alembic_version" >&2\n'
            "    exit 1\n"
            "  fi\n"
            "  cat __ALEMBIC_OUT__\n"
            "  exit 0\n"
            "fi\n"
            "if [ -e __DB_FAIL__ ]; then\n"
            '  echo "FAILED: OperationalError: stub rejected the connection" >&2\n'
            '  echo "second line of the reason" >&2\n'
            '  echo "(Background on this error at: https://sqlalche.me/e/21/e3q8)" >&2\n'
            "  exit 1\n"
            "fi\n"
            'echo "OK postgresql+psycopg://presagio:***@127.0.0.1:5432/presagio:'
            ' PostgreSQL 16.15 stub"\n',
            LOG=self.log,
            ALEMBIC_FAIL=self.alembic_fail_marker,
            ALEMBIC_OUT=self.alembic_out,
            DB_FAIL=self.db_fail_marker,
        )

    def set_db_check(self, *, ok: bool) -> None:
        """Make the fake `python -m presagio.db.session` succeed or fail."""
        if ok:
            self.db_fail_marker.unlink(missing_ok=True)
        else:
            self.db_fail_marker.touch()

    def set_alembic_current(self, output: str) -> None:
        """What the fake `alembic current` prints on stdout ("" = no alembic_version table)."""
        self.alembic_out.write_text(output)

    def env(self, **extra: str) -> dict[str, str]:
        """Environment of a cloud session; DATABASE_URL="" or PRESAGIO_REQUIRE_DB="" drops one."""
        env = {
            "PATH": f"{self.bin}:{SYSTEM_PATH}",
            "HOME": str(self.project),
            "CLAUDE_CODE_REMOTE": "true",
            "CLAUDE_PROJECT_DIR": str(self.project),
            "CLAUDE_ENV_FILE": str(self.env_file),
            "DATABASE_URL": "postgresql+psycopg://presagio:x@127.0.0.1:5432/presagio",
            "PRESAGIO_REQUIRE_DB": "1",
        }
        env.update(extra)
        return {key: value for key, value in env.items() if value != ""}

    def calls(self) -> list[str]:
        """Every stub invocation, in order."""
        return self.log.read_text().splitlines() if self.log.exists() else []


@pytest.fixture
def cloud(tmp_path: Path) -> CloudStubs:
    """A fake cloud VM whose PostgreSQL is already up and whose database is at head."""
    stubs = CloudStubs(tmp_path)
    stubs.pg_up_marker.touch()
    return stubs


def one_line_each(result: subprocess.CompletedProcess[str]) -> str:
    """Assert the failure contract (one identical line on stdout and stderr); return the line."""
    (line,) = result.stdout.splitlines()
    assert result.stderr.splitlines() == [line]
    return line


# --- files -------------------------------------------------------------------------------------


@pytest.mark.parametrize("script", [SETUP, HOOK], ids=["cloud-setup.sh", "session-start.sh"])
def test_scripts_are_executable_bash(script: Path) -> None:
    """Both scripts parse (`bash -n`), are executable and start with a bash shebang."""
    assert script.stat().st_mode & stat.S_IXUSR, f"{script.name} is not executable"
    assert script.read_text().startswith("#!/usr/bin/env bash\n")
    subprocess.run([BASH, "-n", str(script)], check=True)


def test_settings_json_registers_only_the_cloud_session_hook() -> None:
    """.claude/settings.json is shared by every session: one SessionStart hook and nothing else."""
    settings = json.loads(SETTINGS.read_text())
    assert set(settings) == {"hooks"}
    assert set(settings["hooks"]) == {"SessionStart"}
    (group,) = settings["hooks"]["SessionStart"]
    assert group["matcher"] == "startup|resume"
    (hook,) = group["hooks"]
    assert hook["type"] == "command"
    assert "$CLAUDE_PROJECT_DIR" in hook["command"]
    assert hook["command"].endswith("/scripts/cloud/session-start.sh")
    assert (REPO_ROOT / "scripts" / "cloud" / "session-start.sh") == HOOK


def test_readme_quotes_the_values_the_scripts_use() -> None:
    """The values a person pastes into claude.ai/code match scripts/cloud/db.conf."""
    readme = README.read_text()
    assert f"DATABASE_URL={CLOUD_URL}" in readme
    assert "PRESAGIO_REQUIRE_DB=1" in readme
    assert "bash scripts/cloud-setup.sh" in readme
    assert "presagio-dev" in readme


def test_readme_setup_field_is_the_path_independent_wrapper() -> None:
    """P-042: the setup field holds the wrapper (the platform's cwd is not the repo root)."""
    readme = README.read_text()
    assert 'f="${CLAUDE_PROJECT_DIR:-}/scripts/cloud-setup.sh"' in readme
    assert "-path '*/scripts/cloud-setup.sh'" in readme
    assert 'bash "$f"' in readme
    assert "/remote-env" in readme
    assert "gh api" in readme
    assert "0002 (head)" in readme
    # The hook's warning and the README describe the same situation with the same words.
    assert BEHIND in readme
    assert BEHIND in HOOK.read_text()
    header = SETUP.read_text().split("set -euo pipefail", 1)[0]
    assert "Paste one line" not in header
    assert "README" in header and "relative path" in header


@pytest.mark.parametrize("script", [SETUP, HOOK, CONF], ids=["setup", "hook", "conf"])
def test_scripts_never_set_the_odds_api_key(script: Path) -> None:
    """D-052: ODDS_API_KEY is never assigned by cloud scripts (network secret only, if ever)."""
    assert not re.search(r"^\s*(export\s+)?ODDS_API_KEY=", script.read_text(), re.M)


def test_conf_holds_the_documented_non_secret_values() -> None:
    """Role presagio on 127.0.0.1:5432, a cloud-only password, URL built from those parts."""
    assert conf_value("CLOUD_DB_USER") == "presagio"
    assert conf_value("CLOUD_DB_NAME") == "presagio"
    assert conf_value("CLOUD_DB_HOST") == "127.0.0.1"
    assert conf_value("CLOUD_DB_PORT") == "5432"
    assert "not-a-secret" in CLOUD_PASSWORD
    expected = f"postgresql+psycopg://presagio:{CLOUD_PASSWORD}@127.0.0.1:5432/presagio"
    assert expected == CLOUD_URL


# --- hook: local sessions -----------------------------------------------------------------------


@pytest.mark.parametrize("remote", [None, "", "false", "0"], ids=["unset", "empty", "false", "0"])
def test_hook_does_nothing_outside_the_cloud(tmp_path: Path, remote: str | None) -> None:
    """Without CLAUDE_CODE_REMOTE=true: exit 0, no output, and no command run at all."""
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    # PATH holds nothing, so the hook can only get through on bash builtins.
    env = {"PATH": str(empty_bin), "HOME": str(tmp_path)}
    if remote is not None:
        env["CLAUDE_CODE_REMOTE"] = remote
    result = run(HOOK, env)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


# --- hook: cloud sessions -----------------------------------------------------------------------


def test_hook_reports_a_ready_database(cloud: CloudStubs) -> None:
    """PostgreSQL up, DATABASE_URL set, DB at head: one stdout line, nothing on stderr."""
    result = run(HOOK, cloud.env())
    assert result.returncode == 0
    assert result.stderr == ""
    (line,) = result.stdout.splitlines()
    assert line.startswith(f"{TAG} DB ready (PostgreSQL already running). OK postgresql")
    assert "PostgreSQL 16.15 stub" in line
    assert line.endswith("PRESAGIO_REQUIRE_DB=1 (DB tests fail instead of skip).")
    assert "DATABASE_URL was not set" not in line
    assert BEHIND not in line
    # alembic's own stderr logging never reaches the line.
    assert "INFO" not in line
    calls = cloud.calls()
    assert not any(call.startswith("service") for call in calls)
    url = "DATABASE_URL=postgresql+psycopg://presagio:x@127.0.0.1:5432/presagio"
    assert calls[-2:] == [
        f"python -m presagio.db.session {url}",
        f"python -m alembic current {url}",
    ]
    assert cloud.env_file.read_text() == ""


def test_hook_starts_postgres_when_it_is_down(cloud: CloudStubs) -> None:
    """The snapshot keeps no processes: a stopped PostgreSQL is started, then checked."""
    cloud.pg_up_marker.unlink()
    result = run(HOOK, cloud.env())
    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.startswith(f"{TAG} DB ready (started PostgreSQL). OK postgresql")
    calls = cloud.calls()
    started = calls.index("service postgresql start")
    checked = next(i for i, call in enumerate(calls) if call.startswith("python -m presagio"))
    assert started < checked


@pytest.mark.parametrize(
    ("current", "shown"), [("0001\n", "0001"), ("", "<none>")], ids=["behind", "no-table"]
)
def test_hook_warns_when_the_snapshot_database_is_behind_the_repo(
    cloud: CloudStubs, current: str, shown: str
) -> None:
    """`alembic current` without "(head)": the stale-cache warning ends the same single line."""
    cloud.set_alembic_current(current)
    result = run(HOOK, cloud.env())
    assert result.returncode == 0
    assert result.stderr == ""
    (line,) = result.stdout.splitlines()
    assert line.startswith(f"{TAG} DB ready (PostgreSQL already running). OK postgresql")
    assert line.endswith(
        f"PRESAGIO_REQUIRE_DB=1 (DB tests fail instead of skip)."
        f" {BEHIND} (alembic current: {shown}); {FIX}"
    )


def test_hook_reports_an_alembic_failure_in_the_same_line(cloud: CloudStubs) -> None:
    """A crashing `alembic current` (traceback on stderr) becomes one clause with the reason."""
    cloud.alembic_fail_marker.touch()
    result = run(HOOK, cloud.env())
    assert result.returncode == 0
    assert result.stderr == ""
    (line,) = result.stdout.splitlines()
    assert line.startswith(f"{TAG} DB ready")
    # The last informative stderr line survives; the traceback header and INFO logging do not.
    assert line.endswith(
        " alembic current failed"
        f" (exit 1: sqlalchemy.exc.ProgrammingError: stub: relation alembic_version); {FIX}"
    )
    assert "Traceback" not in line
    assert "INFO" not in line


def test_hook_reports_a_failed_check_in_one_line_without_blocking_the_session(
    cloud: CloudStubs,
) -> None:
    """A three-line failure from the checker becomes ONE line on stdout and stderr; exit stays 0."""
    cloud.set_db_check(ok=False)
    result = run(HOOK, cloud.env())
    assert result.returncode == 0
    line = one_line_each(result)
    assert line.startswith(f"{TAG} DB check failed (PostgreSQL already running): FAILED:")
    assert (
        "stub rejected the connection second line of the reason"
        " (Background on this error at: https://sqlalche.me/e/21/e3q8)."
    ) in line
    assert line.endswith("Fix: bash scripts/cloud-setup.sh")
    # No point asking alembic when the connection itself failed.
    assert not any("alembic" in call for call in cloud.calls())


def test_hook_reports_when_postgres_cannot_start(cloud: CloudStubs) -> None:
    """`service postgresql start` failing is reported; the check is not attempted."""
    cloud.pg_up_marker.unlink()
    write_stub(cloud.bin, "service", "exit 1\n")
    result = run(HOOK, cloud.env())
    assert result.returncode == 0
    line = one_line_each(result)
    assert line.startswith(f"{TAG} could not start PostgreSQL")
    assert "bash scripts/cloud-setup.sh" in line
    assert not any(call.startswith("python") for call in cloud.calls())


def test_hook_falls_back_to_the_cloud_default_url(cloud: CloudStubs) -> None:
    """No DATABASE_URL in the environment: the db.conf URL is used, persisted, and announced."""
    result = run(HOOK, cloud.env(DATABASE_URL=""))
    assert result.returncode == 0
    (line,) = result.stdout.splitlines()
    assert line.startswith(f"{TAG} DB ready")
    assert "DATABASE_URL was not set in the environment" in line
    assert all(call.endswith(f"DATABASE_URL={CLOUD_URL}") for call in cloud.calls()[-2:])
    assert cloud.env_file.read_text() == f"export DATABASE_URL={CLOUD_URL}\n"


def test_hook_warns_when_require_db_is_off(cloud: CloudStubs) -> None:
    """Without PRESAGIO_REQUIRE_DB=1 the line says DB tests could skip silently."""
    result = run(HOOK, cloud.env(PRESAGIO_REQUIRE_DB=""))
    assert result.returncode == 0
    assert "PRESAGIO_REQUIRE_DB is not 1" in result.stdout


# --- setup script: success path ---------------------------------------------------------------


class FakeVM:
    """A copy of the setup script in a fake repository, with every cloud command stubbed.

    The script locates the repository from its own path, so copying it (and db.conf) into a
    temporary tree keeps `cd "$REPO"` and `.venv/bin/python` away from the real checkout.
    """

    def __init__(self, tmp_path: Path) -> None:
        self.repo = tmp_path / "repo"
        self.bin = tmp_path / "bin"
        self.home = tmp_path / "home"
        self.log = tmp_path / "calls.log"
        self.sql = tmp_path / "psql-stdin.sql"
        self.pg_up_marker = tmp_path / "pg-up"
        self.role_state = tmp_path / "role-state.txt"
        for directory in (self.repo / "scripts" / "cloud", self.repo / ".venv" / "bin", self.bin):
            directory.mkdir(parents=True)
        self.home.mkdir()
        self.script = self.repo / "scripts" / "cloud-setup.sh"
        shutil.copy(SETUP, self.script)
        shutil.copy(CONF, self.repo / "scripts" / "cloud" / "db.conf")
        self.role_state.write_text("presagio createdb=true\n")
        write_stub(
            self.repo / ".venv" / "bin",
            "python",
            'echo "python $* pwd=$PWD" >> __LOG__\necho "Python 3.12.13 stub"\n',
            LOG=self.log,
        )
        write_stub(
            self.bin,
            "uv",
            'echo "uv $* pwd=$PWD DATABASE_URL=${DATABASE_URL:-}" >> __LOG__\n'
            'case "$1" in\n'
            '  --version) echo "uv 0.11.32 stub" ;;\n'
            "  sync) ;;\n"
            '  run) [ "${4:-}" = current ] && echo "0002 (head)" ;;\n'
            "  *) exit 9 ;;\n"
            "esac\n"
            "exit 0\n",
            LOG=self.log,
        )
        write_stub(
            self.bin,
            "pg_isready",
            'echo "pg_isready $*" >> __LOG__\n[ -e __PG_UP__ ]\n',
            LOG=self.log,
            PG_UP=self.pg_up_marker,
        )
        write_stub(
            self.bin,
            "service",
            'echo "service $*" >> __LOG__\n'
            '[ "$1 $2" = "postgresql start" ] && touch __PG_UP__ && exit 0\nexit 1\n',
            LOG=self.log,
            PG_UP=self.pg_up_marker,
        )
        # runuser -u postgres -- psql ...: `-Atqc 'SHOW server_version'` answers a version; the
        # role/database call feeds SQL on stdin, which is recorded for the assertions.
        write_stub(
            self.bin,
            "runuser",
            'echo "runuser $*" >> __LOG__\n'
            'case " $* " in\n'
            '  *" -Atqc "*) echo "16.15 (Ubuntu 16.15-0ubuntu0.24.04.1) stub" ;;\n'
            "  *) cat >> __SQL__ ;;\n"
            "esac\n",
            LOG=self.log,
            SQL=self.sql,
        )
        # psql over TCP as the application role: the CREATEDB proof.
        write_stub(
            self.bin,
            "psql",
            'echo "psql $* PGPASSWORD=${PGPASSWORD:-} PGCONNECT_TIMEOUT=${PGCONNECT_TIMEOUT:-}"'
            " >> __LOG__\n"
            "cat __ROLE_STATE__\n",
            LOG=self.log,
            ROLE_STATE=self.role_state,
        )

    def env(self) -> dict[str, str]:
        """The platform runs the script as root from some directory: only PATH and HOME matter."""
        return {"PATH": f"{self.bin}:{SYSTEM_PATH}", "HOME": str(self.home)}

    def run(self) -> subprocess.CompletedProcess[str]:
        """Run the copied setup script from a directory that is NOT the repository root."""
        return subprocess.run(
            [BASH, str(self.script)],
            cwd=self.home,
            env=self.env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def calls(self) -> list[str]:
        """Every stub invocation, in order."""
        return self.log.read_text().splitlines() if self.log.exists() else []

    def reset_logs(self) -> None:
        """Forget the recorded calls and SQL (the fake VM's state stays)."""
        self.log.unlink(missing_ok=True)
        self.sql.unlink(missing_ok=True)


@pytest.fixture
def vm(tmp_path: Path) -> FakeVM:
    """A fake cloud VM before setup: PostgreSQL stopped, uv present."""
    return FakeVM(tmp_path)


def test_setup_success_path_runs_every_step_with_the_documented_arguments(vm: FakeVM) -> None:
    """Cold run: uv → uv sync --locked → postgres → role/db (CREATEDB) → alembic head, exit 0."""
    result = vm.run()
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    steps = re.findall(r"^==> \[\d+s\] (.+)$", result.stdout, re.M)
    assert steps == [
        "uv",
        "uv sync --locked",
        "postgresql start",
        "role and database",
        "alembic upgrade head",
    ]
    assert re.search(
        r"^cloud-setup\.sh: OK in \d+s \(database postgresql\+psycopg://presagio:\*\*\*@"
        r"127\.0\.0\.1:5432/presagio\)$",
        result.stdout.splitlines()[-1],
    )
    assert "alembic: 0002 (head)" in result.stdout
    assert "role/db: presagio createdb=true, database presagio" in result.stdout

    repo = str(vm.repo)
    calls = vm.calls()
    expected_in_order = [
        f"uv --version pwd={vm.home} DATABASE_URL=",
        f"uv sync --locked pwd={repo} DATABASE_URL=",
        f"python --version pwd={repo}",
        "service postgresql start",
        "pg_isready -q -h 127.0.0.1 -p 5432",
        "runuser -u postgres -- psql -Atqc SHOW server_version",
        "runuser -u postgres -- psql -v ON_ERROR_STOP=1 -Atq -d postgres"
        f" -v role=presagio -v pw={CLOUD_PASSWORD} -v db=presagio",
        "psql -h 127.0.0.1 -p 5432 -U presagio -d presagio -Atqc SELECT current_user ||"
        " ' createdb=' || rolcreatedb::text FROM pg_roles WHERE rolname = current_user"
        f" PGPASSWORD={CLOUD_PASSWORD} PGCONNECT_TIMEOUT=5",
        f"uv run --locked alembic upgrade head pwd={repo} DATABASE_URL={CLOUD_URL}",
        f"uv run --locked alembic current pwd={repo} DATABASE_URL={CLOUD_URL}",
    ]
    positions = [calls.index(call) for call in expected_in_order]
    assert positions == sorted(positions), calls
    assert not any(call.startswith("python3") for call in calls), "uv was present: no pip"

    sql = vm.sql.read_text()
    assert "CREATE ROLE %I LOGIN CREATEDB PASSWORD %L" in sql
    assert "WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'role')" in sql
    assert "ALTER ROLE %I LOGIN CREATEDB PASSWORD %L" in sql
    assert "CREATE DATABASE %I OWNER %I" in sql
    assert "WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'db')" in sql
    assert sql.count(r"\gexec") == 3


def test_setup_runs_the_same_steps_a_second_time(vm: FakeVM) -> None:
    """Idempotence: a re-run on the prepared VM exits 0 and issues the same guarded commands."""
    first = vm.run()
    assert first.returncode == 0, first.stderr
    first_calls, first_sql = vm.calls(), vm.sql.read_text()
    vm.reset_logs()

    second = vm.run()
    assert second.returncode == 0, second.stderr
    assert second.stderr == ""
    assert second.stdout.splitlines()[-1].startswith("cloud-setup.sh: OK in ")
    assert vm.calls() == first_calls
    # The SQL is guarded (WHERE NOT EXISTS) and ALTER ROLE repairs, so re-sending it is safe.
    assert vm.sql.read_text() == first_sql


def test_setup_fails_when_the_role_has_no_createdb(vm: FakeVM) -> None:
    """The TCP proof must say createdb=true (tests create temporary databases, D-030)."""
    vm.role_state.write_text("presagio createdb=false\n")
    result = vm.run()
    assert result.returncode != 0
    assert result.stderr.splitlines()[-1] == (
        "cloud-setup.sh: FAILED at role and database:"
        " unexpected role state: presagio createdb=false"
    )
    assert not any("alembic" in call for call in vm.calls())


# --- setup script: failure contract -----------------------------------------------------------


def _setup_env(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """A PATH with only a stub dir and coreutils, and a HOME without ~/.local/bin/uv."""
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    (tmp_path / "home").mkdir()
    return stub_bin, {"PATH": f"{stub_bin}:{SYSTEM_PATH}", "HOME": str(tmp_path / "home")}


def test_setup_fails_with_step_and_reason_when_uv_sync_fails(tmp_path: Path) -> None:
    """Non-zero exit and one `FAILED at <step>: <reason>` line; nothing after the failed step."""
    stub_bin, env = _setup_env(tmp_path)
    write_stub(
        stub_bin, "uv", '[ "$1" = "--version" ] && { echo "uv 0.0.0-stub"; exit 0; }\nexit 3\n'
    )
    result = run(SETUP, env)
    assert result.returncode != 0
    assert result.stderr.splitlines()[-1].startswith(
        "cloud-setup.sh: FAILED at uv sync --locked: uv sync --locked failed"
    )
    assert "==> [" in result.stdout
    assert "postgresql start" not in result.stdout


def test_setup_fails_when_uv_is_missing_and_cannot_be_installed(tmp_path: Path) -> None:
    """No uv on PATH and the PyPI install failing is reported as the uv step."""
    stub_bin, env = _setup_env(tmp_path)
    write_stub(stub_bin, "python3", "exit 1\n")
    result = run(SETUP, env)
    assert result.returncode != 0
    assert result.stderr.splitlines()[-1].startswith(
        "cloud-setup.sh: FAILED at uv: could not install uv from PyPI"
    )
