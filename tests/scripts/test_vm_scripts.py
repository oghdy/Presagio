"""scripts/vm/ (P-044, D-058): the collector VM's setup, timers, backup and copy scripts.

No network, no database and no VM: docker, ssh and scp are stubs on PATH that answer like the
Mac or the VM side and log their arguments. The systemd and compose files are checked as text
against the settings and wrapper constants they must agree with.
"""

from __future__ import annotations

import gzip
import importlib.util
import os
import shutil
import stat
import subprocess
import sys
import time as clock
from datetime import time, timedelta
from pathlib import Path

import pytest

from presagio.config import DEFAULT_ODDS_CLOSING_TIMES_ET, Settings, parse_clock_times

REPO_ROOT = Path(__file__).resolve().parents[2]
VM = REPO_ROOT / "scripts" / "vm"
WRAPPER = REPO_ROOT / "scripts" / "launchd" / "presagio_launchd.py"
BASH = shutil.which("bash") or "/bin/bash"
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
SCRIPTS = sorted(VM.glob("*.sh"))
TABLES = [
    "odds_snapshots",
    "ingestion_runs",
    "games",
    "team_game_stats",
    "predictions",
    "evaluations",
    "model_versions",
]


def load_wrapper():
    """The launchd wrapper module (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location("presagio_launchd_vm", WRAPPER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pl = load_wrapper()


def unit_values(name: str, key: str) -> list[str]:
    """Every `key=value` value of a systemd unit file in scripts/vm/."""
    lines = (VM / name).read_text().splitlines()
    return [line.split("=", 1)[1] for line in lines if line.startswith(f"{key}=")]


def env_template() -> dict[str, str]:
    """env.vm.template as a dict (comments and blank lines dropped)."""
    out = {}
    for line in (VM / "env.vm.template").read_text().splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            out[key] = value
    return out


def write_stub(directory: Path, name: str, body: str) -> None:
    """An executable bash stub on the test PATH."""
    path = directory / name
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def run(script: Path, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run a script with bash and exactly the given environment."""
    return subprocess.run(
        [BASH, str(script), *args],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


# --- every script ---------------------------------------------------------------------------------


def test_the_scripts_are_all_there() -> None:
    """The seven files the ticket names, plus the five units and the override."""
    assert [p.name for p in SCRIPTS] == [
        "backup.sh",
        "db-copy-from-mac.sh",
        "deploy.sh",
        "pull-backup.sh",
        "setup.sh",
    ]
    for name in (
        "compose.vm.yml",
        "env.vm.template",
        "presagio-odds.service",
        "presagio-odds.timer",
        "presagio-backup.service",
        "presagio-backup.timer",
        "docker-after-tailscale.conf",
    ):
        assert (VM / name).is_file(), name


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_script_header_and_syntax(script: Path) -> None:
    """Shebang, a usage comment before `set -euo pipefail`, and `bash -n` passes."""
    lines = script.read_text().splitlines()
    assert lines[0] == "#!/usr/bin/env bash"
    first_code = next(i for i, line in enumerate(lines[1:], 1) if not line.startswith("#"))
    assert first_code >= 5 and lines[first_code] == "set -euo pipefail"
    assert subprocess.run([BASH, "-n", str(script)], check=False).returncode == 0


# --- the timer agrees with the settings and the wrapper -------------------------------------------


def expected_oncalendar() -> list[str]:
    """ODDS_CLOSING_TIMES_ET every day, WEEKEND_CLOSING_ET on Saturday and Sunday, all ET."""
    daily = [
        f"*-*-* {t}:00 America/New_York" for t in parse_clock_times(DEFAULT_ODDS_CLOSING_TIMES_ET)
    ]
    weekend = [f"Sat,Sun *-*-* {t:%H:%M}:00 America/New_York" for t in pl.WEEKEND_CLOSING_ET]
    return daily + weekend


def test_timer_fires_at_the_wrapper_slots_in_eastern_time() -> None:
    """Five OnCalendar lines from the two sources; second accuracy; no catch-up (D-048)."""
    assert unit_values("presagio-odds.timer", "OnCalendar") == expected_oncalendar()
    assert unit_values("presagio-odds.timer", "AccuracySec") == ["1s"]
    assert unit_values("presagio-odds.timer", "Persistent") == ["false"]
    assert unit_values("presagio-odds.timer", "Unit") == ["presagio-odds.service"]


def test_the_vm_env_keeps_the_default_closing_times() -> None:
    """If the VM .env and the timer disagreed, every timer start would skip."""
    assert env_template()["ODDS_CLOSING_TIMES_ET"] == DEFAULT_ODDS_CLOSING_TIMES_ET


def test_weekend_timer_lines_match_the_wrapper_decision() -> None:
    """Each OnCalendar time is a slot for the wrapper; the full run's time is not on the timer."""
    closing = tuple(time.fromisoformat(t) for t in parse_clock_times(DEFAULT_ODDS_CLOSING_TIMES_ET))
    clocks = [line.split()[-2][:5] for line in unit_values("presagio-odds.timer", "OnCalendar")]
    assert sorted(clocks) == sorted(
        [f"{t:%H:%M}" for t in closing] + [f"{t:%H:%M}" for t in pl.WEEKEND_CLOSING_ET]
    )
    assert "16:00" not in clocks


def test_odds_service_runs_the_wrapper_as_an_odds_only_host() -> None:
    """The unit's command parses with the wrapper's own parser: run, --odds-only-host, no Docker."""
    (exec_start,) = unit_values("presagio-odds.service", "ExecStart")
    argv = exec_start.split()
    assert argv[:6] == [
        "/usr/local/bin/uv",
        "run",
        "--no-sync",
        "--project",
        "/opt/presagio",
        "python",
    ]
    assert argv[6] == "scripts/launchd/presagio_launchd.py"
    args = pl.build_parser().parse_args(argv[7:])
    assert args.command == "run" and args.odds_only_host and args.no_start_docker
    assert str(args.repo) == "/opt/presagio" and not args.dry_run and args.now is None
    assert str(args.log_dir) == "/var/log/presagio" and str(args.state_dir) == "/var/lib/presagio"
    assert unit_values("presagio-odds.service", "WorkingDirectory") == ["/opt/presagio"]
    assert unit_values("presagio-odds.service", "Type") == ["oneshot"]


def test_odds_service_timeout_is_run_timeout_plus_five_minutes() -> None:
    """TimeoutStartSec covers the wrapper's RUN_TIMEOUT and its 90 s database wait."""
    (value,) = unit_values("presagio-odds.service", "TimeoutStartSec")
    assert value.endswith("min")
    assert timedelta(minutes=int(value[:-3])) == pl.RUN_TIMEOUT + timedelta(minutes=5)
    assert timedelta(minutes=int(value[:-3])) > pl.RUN_TIMEOUT + pl.DB_WAIT


def test_backup_timer_is_noon_utc_away_from_the_slots() -> None:
    """12:00 UTC = 21:00 KST, an hour or more from every slot in both EDT and EST."""
    assert unit_values("presagio-backup.timer", "OnCalendar") == ["*-*-* 12:00:00 UTC"]
    for zone_offset in (4, 5):  # EDT, EST: 12:00 UTC is 08:00 / 07:00 ET
        et = 12 - zone_offset
        for clock_time in [*parse_clock_times(DEFAULT_ODDS_CLOSING_TIMES_ET), "14:45", "16:45"]:
            assert abs(int(clock_time[:2]) - et) >= 1
    assert unit_values("presagio-backup.service", "ExecStart") == [
        "/bin/bash /opt/presagio/scripts/vm/backup.sh"
    ]


def test_docker_drop_in_orders_after_tailscale() -> None:
    """Docker waits for tailscaled and for an address on tailscale0 (the 5433 bind)."""
    text = (VM / "docker-after-tailscale.conf").read_text()
    assert "After=tailscaled.service" in text and "Wants=tailscaled.service" in text
    assert "ip -4 -o addr show dev tailscale0" in text


# --- the VM .env template and the compose override ------------------------------------------------


def test_env_template_has_one_password_for_compose_and_database_url() -> None:
    """DATABASE_URL uses the same user, password placeholder, database and port as compose."""
    env = env_template()
    assert env["POSTGRES_PASSWORD"] == "@POSTGRES_PASSWORD@"
    assert env["DATABASE_URL"] == (
        f"postgresql+psycopg://{env['POSTGRES_USER']}:@POSTGRES_PASSWORD@@127.0.0.1:"
        f"{env['POSTGRES_HOST_PORT']}/{env['POSTGRES_DB']}"
    )
    assert env["ODDS_API_KEY"] == ""
    assert env["TAILSCALE_IP"] == "@TAILSCALE_IP@"
    assert env["COMPOSE_PROJECT_NAME"] == "presagio"
    assert env["COMPOSE_FILE"] == "docker-compose.yml:scripts/vm/compose.vm.yml"


def test_rendered_env_is_valid_settings_without_a_key(tmp_path: Path, monkeypatch) -> None:
    """setup.sh's sed result loads in presagio.config: loopback URL, no key, default times."""
    text = (VM / "env.vm.template").read_text()
    rendered = text.replace("@POSTGRES_PASSWORD@", "ab" * 24).replace(
        "@TAILSCALE_IP@", "100.64.0.9"
    )
    (tmp_path / ".env").write_text(rendered)
    for name in ("DATABASE_URL", "ODDS_API_KEY", "ODDS_CLOSING_TIMES_ET", "DB_CONNECT_TIMEOUT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    settings = Settings()
    assert (
        settings.database_url
        == f"postgresql+psycopg://presagio:{'ab' * 24}@127.0.0.1:5433/presagio"
    )
    assert settings.odds_api_key is None
    assert settings.odds_closing_times_et == DEFAULT_ODDS_CLOSING_TIMES_ET
    assert "@" not in rendered.replace(f"presagio:{'ab' * 24}@127", "")


def test_setup_creates_env_from_the_template_with_a_fresh_password() -> None:
    """The password comes from openssl rand, is written once (umask 077) and never printed."""
    text = (VM / "setup.sh").read_text()
    assert 'password="$(openssl rand -hex 24)"' in text
    assert "(umask 077 && sed" in text and 'chmod 600 "$ENV"' in text
    assert 'if [ -f "$ENV" ]; then' in text  # an existing .env is kept
    assert 'echo "$password' not in text and 'cat "$ENV"' not in text


def test_compose_override_binds_tailscale_only() -> None:
    """The VM adds TAILSCALE_IP:5433; nothing binds every address."""
    text = (VM / "compose.vm.yml").read_text()
    assert '"${TAILSCALE_IP:?' in text and ':${POSTGRES_HOST_PORT:-5433}:5432"' in text
    code = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    assert not [line for line in code if "0.0.0.0" in line]


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not installed")
def test_compose_config_merges_the_two_bindings(tmp_path: Path) -> None:
    """`docker compose config` (no daemon): 127.0.0.1 and the Tailscale address, both 5433."""
    env = tmp_path / "vm.env"
    env.write_text("TAILSCALE_IP=100.64.0.9\nPOSTGRES_PASSWORD=x\n")
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--project-directory",
            str(REPO_ROOT),
            "--env-file",
            str(env),
            "-f",
            str(REPO_ROOT / "docker-compose.yml"),
            "-f",
            str(VM / "compose.vm.yml"),
            "config",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "POSTGRES_PASSWORD": "x"},
    )
    if result.returncode != 0 and "Cannot connect" in result.stderr:
        pytest.skip("docker compose needs a daemon here")
    assert result.returncode == 0, result.stderr
    import json

    ports = json.loads(result.stdout)["services"]["postgres"]["ports"]
    assert sorted((p["host_ip"], p["published"], p["target"]) for p in ports) == [
        ("100.64.0.9", "5433", 5432),
        ("127.0.0.1", "5433", 5432),
    ]


# --- refusals before anything happens ---------------------------------------------------------


@pytest.mark.skipif(os.geteuid() == 0, reason="runs as root here")
@pytest.mark.parametrize("name", ["setup.sh", "deploy.sh"])
def test_vm_scripts_refuse_without_root(tmp_path: Path, name: str) -> None:
    """setup.sh and deploy.sh stop at once when not root (nothing installed, nothing changed)."""
    result = run(VM / name, env={"PATH": SYSTEM_PATH, "HOME": str(tmp_path)})
    assert result.returncode == 1
    assert "run as root" in result.stderr


# --- backup.sh ------------------------------------------------------------------------------------


DUMP_OK = (
    "--\n-- PostgreSQL database dump\n--\nCREATE TABLE t ();\n"
    "--\n-- PostgreSQL database dump complete\n--\n"
)


def backup_env(tmp_path: Path, dump: str = DUMP_OK, code: int = 0) -> dict[str, str]:
    """A fake /opt/presagio and a docker stub whose `compose exec ... pg_dump` prints dump."""
    stubs = tmp_path / "bin"
    stubs.mkdir()
    (tmp_path / "app").mkdir()
    (tmp_path / "dump.sql").write_text(dump)
    write_stub(
        stubs,
        "docker",
        f'echo "$*" >> "{tmp_path}/docker.log"\ncat "{tmp_path}/dump.sql"\nexit {code}\n',
    )
    return {
        "PATH": f"{stubs}:{SYSTEM_PATH}",
        "HOME": str(tmp_path),
        "PRESAGIO_APP": str(tmp_path / "app"),
        "PRESAGIO_BACKUP_DIR": str(tmp_path / "backups"),
    }


def test_backup_writes_a_gzip_dump_and_prunes_old_ones(tmp_path: Path) -> None:
    """One presagio-YYYYMMDD-HHMM.sql.gz (600), the container's pg_dump; 20-day-old file gone."""
    env = backup_env(tmp_path)
    backups = tmp_path / "backups"
    backups.mkdir()
    old, recent = (
        backups / "presagio-20260101-1200.sql.gz",
        backups / "presagio-20261020-1200.sql.gz",
    )
    for path, days in ((old, 20), (recent, 5)):
        path.write_bytes(gzip.compress(b"x"))
        stamp = clock.time() - days * 86400
        os.utime(path, (stamp, stamp))
    result = run(VM / "backup.sh", env=env)
    assert result.returncode == 0, result.stderr
    assert not old.exists() and recent.exists()
    (new,) = [p for p in backups.glob("presagio-*.sql.gz") if p not in (recent,)]
    assert len(new.name) == len("presagio-20261029-1200.sql.gz")
    assert gzip.decompress(new.read_bytes()).decode() == DUMP_OK
    assert stat.S_IMODE(new.stat().st_mode) == 0o600
    assert f"deleted {old}" in result.stdout and f"wrote {new}" in result.stdout
    assert not list(backups.glob(".*partial"))
    call = (tmp_path / "docker.log").read_text()
    assert call.startswith(
        'compose exec -T postgres sh -c pg_dump -U "$POSTGRES_USER" --clean --if-exists'
    )


@pytest.mark.parametrize(
    ("dump", "code"),
    [(DUMP_OK, 1), ("--\n-- PostgreSQL database dump\nCREATE TABLE t (", 0)],
    ids=["pg_dump fails", "dump cut short"],
)
def test_backup_failure_keeps_nothing_and_exits_non_zero(
    tmp_path: Path, dump: str, code: int
) -> None:
    """A failing or truncated pg_dump: exit != 0 (journal shows it), no dump and no partial file."""
    env = backup_env(tmp_path, dump=dump, code=code)
    result = run(VM / "backup.sh", env=env)
    assert result.returncode != 0
    backups = tmp_path / "backups"
    assert list(backups.iterdir()) == []


# --- db-copy-from-mac.sh ----------------------------------------------------------------------


COUNTS = "\n".join(
    f"{t}|{n}" for t, n in zip(TABLES, [8336, 163, 6489, 10670, 5288, 5276, 3], strict=True)
)

# The docker stub answers as the Mac, or as the VM when ssh ran it (SIDE=vm). It logs each call.
DOCKER_STUB = """\
side="${SIDE:-mac}"
echo "$side docker $*" >> "__LOG__"
case "$*" in
  *pg_dump*) echo "-- dump from the mac" ;;
  *"max(captured_at)"*) cat "__DIR__/$side.newest" ;;
  *"UNION ALL"*) cat "__DIR__/$side.counts" ;;
  *"version_num"*) echo 0002 ;;
  *" > '"*) cat "__DIR__/later" ;;
  *psql*) cat > "__DIR__/restored.sql" ;;
esac
"""
# ssh runs the remote command locally as the VM, minus its `cd /opt/presagio &&`.
SSH_STUB = """\
echo "ssh $*" >> "__LOG__"
cmd="${@: -1}"
SIDE=vm bash -c "${cmd#cd /opt/presagio && }"
"""


def copy_env(tmp_path: Path, vm_newer: bool = False, vm_counts: str = COUNTS) -> dict[str, str]:
    """Stub docker and ssh; files hold each side's answers."""
    stubs = tmp_path / "bin"
    stubs.mkdir()
    log = tmp_path / "calls.log"
    for name, body in (("docker", DOCKER_STUB), ("ssh", SSH_STUB)):
        write_stub(stubs, name, body.replace("__LOG__", str(log)).replace("__DIR__", str(tmp_path)))
    (tmp_path / "mac.newest").write_text("2026-10-29 01:50:05+00\n")
    (tmp_path / "vm.newest").write_text(
        "2026-10-30 22:45:03+00\n" if vm_newer else "1970-01-01 00:00:00+00\n"
    )
    (tmp_path / "later").write_text("t\n" if vm_newer else "f\n")
    (tmp_path / "mac.counts").write_text(COUNTS + "\n")
    (tmp_path / "vm.counts").write_text(vm_counts + "\n")
    return {"PATH": f"{stubs}:{SYSTEM_PATH}", "HOME": str(tmp_path)}


def test_db_copy_restores_and_prints_matching_counts(tmp_path: Path) -> None:
    """pg_dump on the Mac (read only) -> ssh -> psql on the VM; seven tables, all 'yes', exit 0."""
    result = run(VM / "db-copy-from-mac.sh", "100.64.0.9", env=copy_env(tmp_path))
    assert result.returncode == 0, result.stderr
    calls = (tmp_path / "calls.log").read_text()
    assert "mac docker compose -p presagio -f " in calls
    assert "exec -T postgres pg_dump -U presagio --clean --if-exists presagio" in calls
    assert "ssh -i " + str(tmp_path / ".ssh/presagio_vm") + " -o BatchMode=yes" in calls
    assert "root@100.64.0.9" in calls
    assert (tmp_path / "restored.sql").read_text() == "-- dump from the mac\n"
    for table in TABLES:
        assert any(
            line.startswith(table) and line.endswith("yes") for line in result.stdout.splitlines()
        )
    assert "alembic_version: Mac 0002, VM 0002" in result.stdout
    assert not [
        line
        for line in calls.splitlines()
        if line.startswith("mac") and "psql" in line and "-At" not in line
    ]


def test_db_copy_refuses_when_the_vm_has_newer_captures(tmp_path: Path) -> None:
    """VM odds newer than the Mac's: stop before pg_dump (the copy would erase them)."""
    result = run(VM / "db-copy-from-mac.sh", "100.64.0.9", env=copy_env(tmp_path, vm_newer=True))
    assert result.returncode == 1
    assert "the VM has odds captured after the Mac's newest" in result.stderr
    assert "pg_dump" not in (tmp_path / "calls.log").read_text()


def test_db_copy_force_overrides_the_guard(tmp_path: Path) -> None:
    """--force (after pull-backup.sh) copies anyway; a second argument is the key path."""
    env = copy_env(tmp_path, vm_newer=True)
    result = run(VM / "db-copy-from-mac.sh", "100.64.0.9", "/keys/other", "--force", env=env)
    assert result.returncode == 0, result.stderr
    calls = (tmp_path / "calls.log").read_text()
    assert "pg_dump" in calls and "ssh -i /keys/other " in calls


def test_db_copy_count_mismatch_exits_1(tmp_path: Path) -> None:
    """A table whose counts differ is marked NO and the script exits 1."""
    vm_counts = COUNTS.replace("odds_snapshots|8336", "odds_snapshots|8000")
    result = run(
        VM / "db-copy-from-mac.sh", "100.64.0.9", env=copy_env(tmp_path, vm_counts=vm_counts)
    )
    assert result.returncode == 1
    line = next(line for line in result.stdout.splitlines() if line.startswith("odds_snapshots"))
    assert line.split() == ["odds_snapshots", "8336", "8000", "NO"]


# --- pull-backup.sh -------------------------------------------------------------------------------


def pull_env(tmp_path: Path, latest: str) -> dict[str, str]:
    """ssh prints `latest` (the newest dump's path); scp copies a fake file there."""
    stubs = tmp_path / "bin"
    stubs.mkdir()
    log = tmp_path / "calls.log"
    write_stub(stubs, "ssh", f'echo "ssh $*" >> "{log}"\necho "{latest}"\n')
    write_stub(
        stubs,
        "scp",
        f'echo "scp $*" >> "{log}"\nfor last; do :; done\n'
        f'echo dump > "$last/$(basename "{latest}")"\n',
    )
    return {
        "PATH": f"{stubs}:{SYSTEM_PATH}",
        "HOME": str(tmp_path),
        "PRESAGIO_BACKUP_LOCAL": str(tmp_path / "local"),
    }


def test_pull_backup_copies_the_newest_dump(tmp_path: Path) -> None:
    """The newest /var/backups/presagio dump lands in the local folder and is listed."""
    latest = "/var/backups/presagio/presagio-20261029-1200.sql.gz"
    result = run(VM / "pull-backup.sh", "100.64.0.9", env=pull_env(tmp_path, latest))
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "local" / "presagio-20261029-1200.sql.gz").read_text() == "dump\n"
    assert "presagio-20261029-1200.sql.gz" in result.stdout
    assert f"root@100.64.0.9:{latest}" in (tmp_path / "calls.log").read_text()


def test_pull_backup_without_a_dump_exits_1(tmp_path: Path) -> None:
    """No dump on the VM: one line, exit 1, nothing copied."""
    result = run(VM / "pull-backup.sh", "100.64.0.9", env=pull_env(tmp_path, ""))
    assert result.returncode == 1 and "no dump" in result.stderr
    assert "scp" not in (tmp_path / "calls.log").read_text()
