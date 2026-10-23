"""scripts/launchd/presagio_launchd.py: slot decisions in EDT and EST, markers, log, plist."""

from __future__ import annotations

import importlib.util
import os
import plistlib
import subprocess
import sys
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "launchd" / "presagio_launchd.py"
TEMPLATE = REPO_ROOT / "scripts" / "launchd" / "com.presagio.ingest-daily.plist.template"
SEOUL = ZoneInfo("Asia/Seoul")
EASTERN = ZoneInfo("America/New_York")
FULL_KST = time(16, 0)
CLOSING_ET = (time(18, 45), time(19, 45), time(21, 45))


def load_module() -> ModuleType:
    """Import the script by path (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location("presagio_launchd", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look the module up while building the class
    spec.loader.exec_module(module)
    return module


pl = load_module()


def kst(text: str) -> datetime:
    """'2026-10-24 07:45' in Korea -> aware UTC datetime."""
    return datetime.fromisoformat(text).replace(tzinfo=SEOUL).astimezone(UTC)


def decide(now: datetime):
    """decide() with the default D-038 times."""
    return pl.decide(now, FULL_KST, CLOSING_ET)


def test_calendar_covers_both_offsets() -> None:
    """16:00 plus 18:45/19:45/21:45 ET at KST+13 h (EDT) and KST+14 h (EST): six wake-ups."""
    assert pl.calendar_entries(FULL_KST, CLOSING_ET) == [
        (7, 45),
        (8, 45),
        (9, 45),
        (10, 45),
        (11, 45),
        (16, 0),
    ]


def simulate(start: date, end: date) -> list:
    """Every launchd wake-up between two Korean dates and the slot each one stands for."""
    entries = pl.calendar_entries(FULL_KST, CLOSING_ET)
    slots = []
    day = start
    while day <= end:
        for hour, minute in entries:
            slot = decide(pl.wall_time(day, time(hour, minute), SEOUL))
            if slot is not None:
                slots.append(slot)
        day += timedelta(days=1)
    return slots


def test_exactly_four_runs_a_day_across_both_dst_changes() -> None:
    """2026-10-02..2027-04-29 covers 2026-11-01 (EDT->EST) and 2027-03-14 (EST->EDT)."""
    slots = simulate(date(2026, 10, 1), date(2027, 4, 30))
    assert len({s.key for s in slots}) == len(slots)  # no slot twice
    odds = [s for s in slots if s.kind == pl.ODDS]
    full = [s for s in slots if s.kind == pl.FULL]
    # Closing captures of ET dates fully inside the range; full runs of every Korean date.
    odds_days = Counter(s.base_date for s in odds)
    for day in (date(2026, 10, 2) + timedelta(days=n) for n in range(209)):
        captured = sorted(s.at.astimezone(EASTERN).time() for s in odds if s.base_date == day)
        assert captured == list(CLOSING_ET), day
        assert all(s.at.astimezone(EASTERN).date() == day for s in odds if s.base_date == day)
    assert set(odds_days.values()) == {3}
    assert Counter(s.base_date for s in full) == {
        date(2026, 10, 1) + timedelta(days=n): 1 for n in range(212)
    }
    assert all(s.at.astimezone(SEOUL).time() == FULL_KST for s in full)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        # EDT (until 2026-10-31): 18:45/19:45/21:45 ET = 07:45/08:45/10:45 KST next day.
        ("2026-10-24 07:45", ("odds", date(2026, 10, 23), "18:45")),
        ("2026-10-24 08:45", ("odds", date(2026, 10, 23), "19:45")),
        ("2026-10-24 09:45", None),
        ("2026-10-24 10:45", ("odds", date(2026, 10, 23), "21:45")),
        ("2026-10-24 11:45", None),
        # Night of the change: ET 10-31 is still EDT until 02:00 on 11-01.
        ("2026-11-01 07:45", ("odds", date(2026, 10, 31), "18:45")),
        ("2026-11-01 10:45", ("odds", date(2026, 10, 31), "21:45")),
        ("2026-11-01 11:45", None),
        # EST (from 2026-11-01): = 08:45/09:45/11:45 KST next day.
        ("2026-11-02 07:45", None),
        ("2026-11-02 08:45", ("odds", date(2026, 11, 1), "18:45")),
        ("2026-11-02 09:45", ("odds", date(2026, 11, 1), "19:45")),
        ("2026-11-02 10:45", None),
        ("2026-11-02 11:45", ("odds", date(2026, 11, 1), "21:45")),
        # EDT again from 2027-03-14.
        ("2027-03-15 07:45", ("odds", date(2027, 3, 14), "18:45")),
        ("2027-03-15 11:45", None),
        # Full run, Korean date.
        ("2026-10-23 16:00", ("full", date(2026, 10, 23), "16:00")),
        ("2026-11-02 16:00", ("full", date(2026, 11, 2), "16:00")),
    ],
)
def test_decide_known_wakeups(now: str, expected: tuple | None) -> None:
    """Spot checks around the 2026-11-01 and 2027-03-14 changes."""
    slot = decide(kst(now))
    if expected is None:
        assert slot is None
        return
    kind, base, clock = expected
    zone = EASTERN if kind == "odds" else SEOUL
    assert (slot.kind, slot.base_date, f"{slot.at.astimezone(zone):%H:%M}") == expected


@pytest.mark.parametrize(
    ("offset_min", "runs"),
    [(-11, False), (-10, True), (0, True), (10, True), (11, False)],
)
def test_odds_tolerance_edges(offset_min: int, runs: bool) -> None:
    """A wake-up counts as a closing slot within +-10 min of its ET time, not beyond."""
    at = pl.wall_time(date(2026, 11, 5), time(19, 45), EASTERN)
    slot = decide(at + timedelta(minutes=offset_min))
    assert (slot is not None and slot.kind == pl.ODDS) is runs


def test_full_run_late_same_korean_day_only() -> None:
    """After sleep the full run still runs late that Korean day, never after midnight."""
    assert decide(kst("2026-10-23 15:49")) is None
    assert decide(kst("2026-10-23 15:50")).kind == pl.FULL
    assert decide(kst("2026-10-23 23:59")).base_date == date(2026, 10, 23)
    assert decide(kst("2026-10-24 00:01")) is None


def test_naive_now_is_rejected() -> None:
    """A naive datetime would silently mean the wrong zone."""
    with pytest.raises(ValueError):
        pl.decide(datetime(2026, 10, 23, 16), FULL_KST, CLOSING_ET)


def test_command_lines() -> None:
    """Explicit --date; --odds-only for closing slots; --dry-run passes through."""
    full = decide(kst("2026-10-23 16:00"))
    odds = decide(kst("2026-11-02 08:45"))
    assert pl.command_for(full, "/venv/bin/presagio") == [
        "/venv/bin/presagio",
        "ingest-daily",
        "--date",
        "2026-10-23",
    ]
    assert pl.command_for(odds, "/venv/bin/presagio", dry_run=True)[2:] == [
        "--date",
        "2026-11-01",
        "--odds-only",
        "--dry-run",
    ]


BLOCK_OUTPUT = "\nStopped: the source blocked us (403/429). Wait before running again (D-024).\n"


class FakeRunner:
    """Stands in for subprocess.run: records the call, writes output, returns a code."""

    def __init__(self, code: int, output: str = "", raises: Exception | None = None) -> None:
        self.code = code
        self.output = output or (BLOCK_OUTPUT if code == 2 else "")
        self.raises = raises
        self.calls: list[list[str]] = []

    def __call__(self, cmd, *, cwd, stdout, stderr, env, timeout):
        """Record and pretend to be ingest-daily."""
        assert stderr == subprocess.STDOUT and env["PYTHONUNBUFFERED"] == "1"
        assert timeout == 30 * 60
        self.calls.append(cmd)
        if self.raises is not None:
            raise self.raises
        stdout.write(f"fake ingest-daily output in {cwd}\n{self.output}")
        return SimpleNamespace(returncode=self.code)


def run_at(tmp_path: Path, now: datetime, runner: FakeRunner, **kwargs) -> int:
    """pl.run with tmp log/state folders."""
    return pl.run(
        now=now,
        full_kst=FULL_KST,
        closing_et=CLOSING_ET,
        repo=tmp_path / "repo",
        presagio="/venv/bin/presagio",
        log_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        runner=runner,
        **kwargs,
    )


def read_log(tmp_path: Path, day: str) -> str:
    """The day's log file."""
    return (tmp_path / "logs" / f"ingest-{day}.log").read_text()


@pytest.mark.parametrize(
    ("code", "word"), [(0, "OK"), (1, "FAILED"), (2, "BLOCKED"), (64, "USAGE"), (3, "UNEXPECTED")]
)
def test_result_line_names_the_exit_code(tmp_path: Path, code: int, word: str) -> None:
    """Each exit code gets its own word on a grep-able RESULT line in the day's log."""
    runner = FakeRunner(code)
    assert run_at(tmp_path, kst("2026-10-23 16:00"), runner) == code
    log = read_log(tmp_path, "2026-10-23")
    assert "fake ingest-daily output" in log
    assert f"RESULT exit={code} {word}" in log
    assert "[full-2026-10-23-1600]" in log


def test_slot_runs_once(tmp_path: Path) -> None:
    """A second wake-up for the same slot (kickstart, coalesced wake) does not call again."""
    runner = FakeRunner(0)
    now = kst("2026-10-24 07:45")
    run_at(tmp_path, now, runner)
    run_at(tmp_path, now + timedelta(minutes=3), runner)
    assert len(runner.calls) == 1
    assert "this slot already ran (marker odds-2026-10-23-1845)" in read_log(tmp_path, "2026-10-24")


def test_skip_is_logged_and_calls_nothing(tmp_path: Path) -> None:
    """Off-slot wake-ups (half the odds wake-ups) leave one skip line and exit 0."""
    runner = FakeRunner(0)
    assert run_at(tmp_path, kst("2026-10-24 09:45"), runner) == 0
    assert runner.calls == []
    assert "skip: no slot now (20:45 ET, 09:45 KST)" in read_log(tmp_path, "2026-10-24")


def slots_after_block(tmp_path: Path, blocked_at: str, wakeups: list[str]) -> list[str]:
    """Block the slot at blocked_at (+1.5 s start-up), then report which later wake-ups ran."""
    run_at(tmp_path, kst(blocked_at) + timedelta(seconds=1.5), FakeRunner(2))
    assert (tmp_path / "state" / "blocked").is_file()
    ran = []
    for wake in wakeups:
        later = FakeRunner(0)
        run_at(tmp_path, kst(wake), later)
        if later.calls:
            ran.append(wake)
    return ran


def test_block_at_full_run_skips_until_next_full_run(tmp_path: Path) -> None:
    """Blocked at 16:00: the three closing captures skip, the next day's 16:00 runs.

    The next day's run starts 0.6 s earlier than the blocked one did: slot times, not wall
    clocks, decide, so start-up jitter cannot skip it.
    """
    wakeups = ["2026-10-24 07:45", "2026-10-24 08:45", "2026-10-24 10:45"]
    later = ["2026-10-24 16:00:00.9", "2026-10-25 07:45"]
    assert slots_after_block(tmp_path, "2026-10-23 16:00", wakeups + later) == later
    assert "paused after a block" in read_log(tmp_path, "2026-10-24")


def test_block_at_closing_capture_skips_until_same_slot_tomorrow(tmp_path: Path) -> None:
    """Blocked at the 18:45 ET capture: 19:45, 21:45 and 16:00 KST skip; 18:45 ET next day runs."""
    wakeups = ["2026-10-24 08:45", "2026-10-24 10:45", "2026-10-24 16:00"]
    later = ["2026-10-25 07:45:00.2", "2026-10-25 08:45"]
    assert slots_after_block(tmp_path, "2026-10-24 07:45", wakeups + later) == later


def test_block_pause_length_is_fixed() -> None:
    """23 h minus the slot tolerance: the next same slot is past it even on a 23 h day."""
    assert timedelta(hours=23) - pl.SLOT_TOLERANCE == pl.BLOCK_PAUSE


def test_block_before_spring_change_resumes_next_day_same_slot(tmp_path: Path) -> None:
    """Blocked at 18:45 ET on 2027-03-13 (EST); 18:45 ET on 03-14 (EDT) is 23 h later and runs."""
    wakeups = ["2027-03-14 09:45", "2027-03-14 11:45", "2027-03-14 16:00"]
    later = ["2027-03-15 07:45", "2027-03-15 08:45"]
    assert slots_after_block(tmp_path, "2027-03-14 08:45", wakeups + later) == later


def test_child_is_the_venv_presagio_script() -> None:
    """Never `uv run`: uv's own errors also exit 2 and would read as a block."""
    assert pl.presagio_script() == str(Path(sys.executable).parent / "presagio")


def test_unreadable_block_file_counts_from_its_mtime(tmp_path: Path) -> None:
    """An empty or broken pause file pauses from its mtime instead of crashing every run."""
    state = tmp_path / "state"
    state.mkdir()
    (state / "blocked").write_text("")
    until = pl.blocked_until(state)
    assert until is not None and until > datetime.now(UTC) + timedelta(hours=22)


def test_exit_2_without_block_line_is_not_a_block(tmp_path: Path) -> None:
    """Exit 2 with no ingest-daily block line (not from a 403/429) logs UNEXPECTED, no pause."""
    runner = FakeRunner(2, output="error: Failed to spawn: `presagio`\n")
    assert run_at(tmp_path, kst("2026-10-23 16:00"), runner) == 2
    log = read_log(tmp_path, "2026-10-23")
    assert "RESULT exit=2 UNEXPECTED" in log and "BLOCKED" not in log
    assert not (tmp_path / "state" / "blocked").exists()


def test_block_line_from_an_earlier_run_does_not_count(tmp_path: Path) -> None:
    """Only this run's output is searched for the block line."""
    run_at(tmp_path, kst("2026-10-23 16:00"), FakeRunner(1, output=BLOCK_OUTPUT))
    (tmp_path / "state" / "slots" / "full-2026-10-23-1600").unlink()
    run_at(tmp_path, kst("2026-10-23 16:01"), FakeRunner(2, output="other error\n"))
    assert not (tmp_path / "state" / "blocked").exists()


def test_dry_run_exit_2_writes_no_pause(tmp_path: Path) -> None:
    """A dry run never starts the pause."""
    run_at(tmp_path, kst("2026-10-23 16:00"), FakeRunner(2), dry_run=True)
    assert "RESULT exit=2 BLOCKED" in read_log(tmp_path, "2026-10-23")
    assert not (tmp_path / "state" / "blocked").exists()


def test_timeout_is_logged(tmp_path: Path) -> None:
    """A run killed by the 30 min timeout leaves a TIMEOUT result and exit 124."""
    runner = FakeRunner(0, raises=subprocess.TimeoutExpired(["presagio"], 1800))
    assert run_at(tmp_path, kst("2026-10-23 16:00"), runner) == 124
    assert "RESULT exit=124 TIMEOUT (killed after 30 min)" in read_log(tmp_path, "2026-10-23")


def test_missing_script_is_logged(tmp_path: Path) -> None:
    """The child cannot start (venv gone): one 'cannot start' line and RESULT exit=1."""
    runner = FakeRunner(0, raises=FileNotFoundError(2, "No such file", "/venv/bin/presagio"))
    assert run_at(tmp_path, kst("2026-10-23 16:00"), runner) == 1
    log = read_log(tmp_path, "2026-10-23")
    assert "cannot start /venv/bin/presagio" in log and "RESULT exit=1 FAILED" in log


def test_off_calendar_skip_hints_at_reinstall(tmp_path: Path) -> None:
    """A skip away from every computed wake-up says to rerun install.sh; a normal skip does not."""
    run_at(tmp_path, kst("2026-10-24 09:45"), FakeRunner(0))
    run_at(tmp_path, kst("2026-10-24 13:00"), FakeRunner(0))
    lines = read_log(tmp_path, "2026-10-24").splitlines()
    assert "install.sh" not in lines[0] and "run scripts/launchd/install.sh again" in lines[1]


def test_dry_run_keeps_no_marker(tmp_path: Path) -> None:
    """--dry-run passes through and does not use up the slot."""
    runner = FakeRunner(0)
    now = kst("2026-10-23 16:00")
    run_at(tmp_path, now, runner, dry_run=True)
    run_at(tmp_path, now, runner)
    assert runner.calls[0][-1] == "--dry-run" and runner.calls[1][-1] == "2026-10-23"


def test_old_markers_are_pruned(tmp_path: Path) -> None:
    """Markers older than 14 days are deleted on the next run."""
    slots = tmp_path / "state" / "slots"
    slots.mkdir(parents=True)
    old = slots / "odds-2026-09-01-1845"
    old.write_text("x")
    fifteen_days_ago = (kst("2026-10-23 16:00") - timedelta(days=15)).timestamp()
    os.utime(old, (fifteen_days_ago, fifteen_days_ago))
    run_at(tmp_path, kst("2026-10-23 16:00"), FakeRunner(0))
    assert not old.exists() and (slots / "full-2026-10-23-1600").exists()


def test_rendered_plist_is_valid(tmp_path: Path) -> None:
    """Paths with spaces, '&' and Hangul survive; six calendar entries; no hand-written paths."""
    repo = tmp_path / "잡 플&젝" / "Presagio"
    text = pl.render_plist(
        TEMPLATE.read_text(encoding="utf-8"),
        repo=repo,
        uv="/opt/homebrew/bin/uv",
        wrapper=SCRIPT,
        log_dir=tmp_path / "Logs",
        state_dir=tmp_path / "State dir",
        entries=pl.calendar_entries(FULL_KST, CLOSING_ET),
    )
    assert "@" not in text.replace("Presagio", "")
    data = plistlib.loads(text.encode())
    assert "--uv" not in data["ProgramArguments"]
    assert data["Label"] == "com.presagio.ingest-daily"
    assert data["WorkingDirectory"] == str(repo)
    args = data["ProgramArguments"]
    assert args[0] == "/opt/homebrew/bin/uv" and str(SCRIPT) in args
    assert args[args.index("--repo") + 1] == str(repo)
    assert data["EnvironmentVariables"]["PATH"].startswith("/opt/homebrew/bin:")
    assert [(e["Hour"], e["Minute"]) for e in data["StartCalendarInterval"]] == [
        (7, 45),
        (8, 45),
        (9, 45),
        (10, 45),
        (11, 45),
        (16, 0),
    ]
    assert data["RunAtLoad"] is False


def test_settings_error_hides_values() -> None:
    """Only the error type and setting names reach the log."""
    from pydantic import ValidationError

    from presagio.config import Settings

    with pytest.raises(ValidationError) as info:
        Settings(database_url="postgresql://u:secret@h/db", odds_closing_times_et="25:00")
    line = pl.settings_error(info.value)
    assert line == "ValidationError in odds_closing_times_et"
    assert "secret" not in line


def run_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *extra: str) -> int:
    """pl.main(['run', ...]) from an empty folder (no .env)."""
    from presagio.config import get_settings

    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    try:
        return pl.main(
            [
                "run",
                "--repo",
                str(tmp_path),
                "--log-dir",
                str(tmp_path / "logs"),
                "--state-dir",
                str(tmp_path / "state"),
                *extra,
            ]
        )
    finally:
        get_settings.cache_clear()


def test_main_settings_error_logs_no_values(tmp_path: Path, monkeypatch) -> None:
    """A bad setting gives one RESULT exit=1 FAILED line with the setting name, no values."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:SECRETPW123@127.0.0.1:1/db")
    monkeypatch.setenv("ODDS_CLOSING_TIMES_ET", "25:00")
    code = run_main(tmp_path, monkeypatch, "--uv", "/old/plist/uv")  # old plists pass --uv
    assert code == 1
    (log,) = (tmp_path / "logs").glob("ingest-*.log")
    text = log.read_text()
    assert "RESULT exit=1 FAILED (settings: ValidationError in odds_closing_times_et)" in text
    assert "SECRETPW123" not in text and "25:00" not in text


def test_main_now_needs_dry_run(tmp_path: Path, monkeypatch) -> None:
    """--now without --dry-run is refused before anything runs."""
    with pytest.raises(SystemExit):
        run_main(tmp_path, monkeypatch, "--now", "2026-10-23T16:00+09:00")
    assert not (tmp_path / "logs").exists()
