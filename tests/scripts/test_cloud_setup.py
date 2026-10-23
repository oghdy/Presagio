"""scripts/cloud-setup.sh, scripts/cloud/session-start.sh and .claude/settings.json (P-040).

No network and no database: the cloud-only commands (pg_isready, service, uv, the venv python)
are replaced by stubs on PATH or in a temporary project directory.
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
# Real coreutils (dirname, seq, cd ...) but no real pg_isready/service/uv: the stub dir goes first.
SYSTEM_PATH = "/usr/bin:/bin"
TAG = "[presagio cloud]"


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


def write_stub(directory: Path, name: str, body: str) -> Path:
    """Write an executable bash stub called `name` into `directory`."""
    path = directory / name
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def run(script: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run a script with bash and exactly the given environment."""
    return subprocess.run(
        [BASH, str(script)], env=env, capture_output=True, text=True, timeout=60, check=False
    )


class CloudStubs:
    """A fake cloud VM: stub commands on PATH and a fake project with a fake .venv python."""

    def __init__(self, tmp_path: Path) -> None:
        self.bin = tmp_path / "bin"
        self.project = tmp_path / "project"
        self.log = tmp_path / "calls.log"
        self.env_file = tmp_path / "claude.env"
        self.pg_up_marker = tmp_path / "pg-up"
        self.bin.mkdir()
        (self.project / ".venv" / "bin").mkdir(parents=True)
        self.env_file.touch()
        # pg_isready is "ready" only while the marker exists; the `service` stub recreates it.
        write_stub(
            self.bin,
            "pg_isready",
            f'echo "pg_isready $*" >> "{self.log}"\n[ -e "{self.pg_up_marker}" ]\n',
        )
        write_stub(
            self.bin,
            "service",
            f'echo "service $*" >> "{self.log}"\n'
            f'[ "$1 $2" = "postgresql start" ] && touch "{self.pg_up_marker}" && exit 0\nexit 1\n',
        )
        self.set_db_check(ok=True)

    def set_db_check(self, *, ok: bool) -> None:
        """Make the fake venv python succeed or fail like `python -m presagio.db.session`."""
        body = f'echo "python $* DATABASE_URL=$DATABASE_URL" >> "{self.log}"\n'
        if ok:
            body += (
                'echo "OK postgresql+psycopg://presagio:***@127.0.0.1:5432/presagio:'
                ' PostgreSQL 16.15 stub"\n'
            )
        else:
            body += 'echo "FAILED: OperationalError: stub rejected the connection" >&2\nexit 1\n'
        write_stub(self.project / ".venv" / "bin", "python", body)

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
    """A fake cloud VM whose PostgreSQL is already up."""
    stubs = CloudStubs(tmp_path)
    stubs.pg_up_marker.touch()
    return stubs


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
    password = conf_value("CLOUD_DB_PASSWORD")
    assert "not-a-secret" in password
    expected = f"postgresql+psycopg://presagio:{password}@127.0.0.1:5432/presagio"
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
    """PostgreSQL up, DATABASE_URL set: one stdout line for Claude, nothing on stderr."""
    result = run(HOOK, cloud.env())
    assert result.returncode == 0
    assert result.stderr == ""
    lines = result.stdout.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith(f"{TAG} DB ready (PostgreSQL already running). OK postgresql")
    assert "PostgreSQL 16.15 stub" in lines[0]
    assert "PRESAGIO_REQUIRE_DB=1" in lines[0]
    assert "DATABASE_URL was not set" not in lines[0]
    calls = cloud.calls()
    assert not any(call.startswith("service") for call in calls)
    assert calls[-1] == (
        "python -m presagio.db.session "
        "DATABASE_URL=postgresql+psycopg://presagio:x@127.0.0.1:5432/presagio"
    )
    assert cloud.env_file.read_text() == ""


def test_hook_starts_postgres_when_it_is_down(cloud: CloudStubs) -> None:
    """The snapshot keeps no processes: a stopped PostgreSQL is started, then checked."""
    cloud.pg_up_marker.unlink()
    result = run(HOOK, cloud.env())
    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.startswith(f"{TAG} DB ready (started PostgreSQL). OK postgresql")
    calls = cloud.calls()
    assert "service postgresql start" in calls
    assert calls.index("service postgresql start") < len(calls) - 1
    assert calls[-1].startswith("python -m presagio.db.session")


def test_hook_reports_a_failed_check_without_blocking_the_session(cloud: CloudStubs) -> None:
    """A rejected connection is reported on stdout (Claude) and stderr (human); exit stays 0."""
    cloud.set_db_check(ok=False)
    result = run(HOOK, cloud.env())
    assert result.returncode == 0
    (line,) = result.stdout.splitlines()
    assert line.startswith(f"{TAG} DB check failed (PostgreSQL already running): FAILED:")
    assert "stub rejected the connection" in line
    assert "bash scripts/cloud-setup.sh" in line
    assert result.stderr.strip() == line


def test_hook_reports_when_postgres_cannot_start(cloud: CloudStubs) -> None:
    """`service postgresql start` failing is reported; the check is not attempted."""
    cloud.pg_up_marker.unlink()
    write_stub(cloud.bin, "service", "exit 1\n")
    result = run(HOOK, cloud.env())
    assert result.returncode == 0
    (line,) = result.stdout.splitlines()
    assert line.startswith(f"{TAG} could not start PostgreSQL")
    assert "bash scripts/cloud-setup.sh" in line
    assert result.stderr.strip() == line
    assert not any(call.startswith("python") for call in cloud.calls())


def test_hook_falls_back_to_the_cloud_default_url(cloud: CloudStubs) -> None:
    """No DATABASE_URL in the environment: the db.conf URL is used, persisted, and announced."""
    result = run(HOOK, cloud.env(DATABASE_URL=""))
    assert result.returncode == 0
    (line,) = result.stdout.splitlines()
    assert line.startswith(f"{TAG} DB ready")
    assert "DATABASE_URL was not set in the environment" in line
    assert cloud.calls()[-1].endswith(f"DATABASE_URL={CLOUD_URL}")
    assert cloud.env_file.read_text() == f"export DATABASE_URL={CLOUD_URL}\n"


def test_hook_warns_when_require_db_is_off(cloud: CloudStubs) -> None:
    """Without PRESAGIO_REQUIRE_DB=1 the line says DB tests could skip silently."""
    result = run(HOOK, cloud.env(PRESAGIO_REQUIRE_DB=""))
    assert result.returncode == 0
    assert "PRESAGIO_REQUIRE_DB is not 1" in result.stdout


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
