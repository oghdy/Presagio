"""scripts/launchd/presagio_launchd.py: slots (EDT/EST, weekends), markers, log, plist, DB wait."""

from __future__ import annotations

import importlib.util
import os
import plistlib
import socket
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
WEEKEND_ET = (time(14, 45), time(16, 45))  # D-027 as amended (D-049 M-9), Saturday and Sunday ET


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


def test_weekend_times_are_the_amended_d027_ones() -> None:
    """The wrapper's constant is what D-027 (amended by D-049 M-9) says."""
    assert pl.WEEKEND_CLOSING_ET == WEEKEND_ET


def test_calendar_covers_both_offsets() -> None:
    """16:00 plus 14:45/16:45/18:45/19:45/21:45 ET at KST+13 h (EDT) and +14 h (EST): ten."""
    assert pl.calendar_entries(FULL_KST, CLOSING_ET) == [
        (3, 45),
        (4, 45),
        (5, 45),
        (6, 45),
        (7, 45),
        (8, 45),
        (9, 45),
        (10, 45),
        (11, 45),
        (16, 0),
    ]


def test_empty_closing_times_turn_off_the_weekend_captures_too() -> None:
    """ODDS_CLOSING_TIMES_ET='' (no closing captures, D-058 addendum 3) leaves only 16:00."""
    assert pl.calendar_entries(FULL_KST, ()) == [(16, 0)]
    assert pl.decide(kst("2026-11-01 03:45"), FULL_KST, ()) is None  # Saturday 14:45 EDT


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


def test_four_runs_on_weekdays_six_on_weekends_across_both_dst_changes() -> None:
    """Base dates 2026-11-01..2027-06-01 cover 2026-11-01 (EDT->EST) and 2027-03-14 (EST->EDT).

    Per base date D: the full run of Korean date D, the closing captures of ET date D (they fall
    on the next Korean morning, so one more Korean day is simulated). Saturday and Sunday are the
    ET date's weekday; both change days are Sundays.
    """
    first, last = date(2026, 11, 1), date(2027, 6, 1)
    slots = simulate(first, last + timedelta(days=1))
    assert len({s.key for s in slots}) == len(slots)  # no slot twice
    assert len({s.at for s in slots}) == len(slots)  # no two slots at the same moment
    odds = Counter(s.base_date for s in slots if s.kind == pl.ODDS)
    full = Counter(s.base_date for s in slots if s.kind == pl.FULL)
    runs_per_day = {}
    day = first
    while day <= last:
        weekend = day.weekday() in (5, 6)
        captured = sorted(
            s.at.astimezone(EASTERN).time()
            for s in slots
            if s.kind == pl.ODDS and s.base_date == day
        )
        assert captured == sorted(CLOSING_ET + WEEKEND_ET if weekend else CLOSING_ET), day
        assert all(
            s.at.astimezone(EASTERN).date() == day
            for s in slots
            if s.kind == pl.ODDS and s.base_date == day
        )
        assert full[day] == 1, day
        runs_per_day[day] = full[day] + odds[day]
        day += timedelta(days=1)
    assert all(s.at.astimezone(SEOUL).time() == FULL_KST for s in slots if s.kind == pl.FULL)
    weekend_days = {d for d in runs_per_day if d.weekday() in (5, 6)}
    assert {runs_per_day[d] for d in weekend_days} == {6}
    assert {runs_per_day[d] for d in runs_per_day.keys() - weekend_days} == {4}
    assert runs_per_day[date(2026, 11, 1)] == runs_per_day[date(2027, 3, 14)] == 6
    assert len(runs_per_day) == 213 and len(weekend_days) == 61


def test_monthly_credits_stay_under_the_free_quota() -> None:
    """2026-11..2027-06: (4 x days + 2 x weekend days) captures x 3 credits, every month < 500.

    The worst months (31 days, 10 Saturdays and Sundays: 2027-01 and 2027-05) cost 432.
    """
    totals = pl.month_totals(date(2026, 11, 1), 242, FULL_KST, CLOSING_ET)
    captures = {month: n[pl.FULL] + n[pl.ODDS] for month, n in totals.items()}
    for month, n in totals.items():
        assert captures[month] == 4 * n["days"] + 2 * n["weekend_days"], month
    credits = {month: count * pl.CREDITS_PER_CAPTURE for month, count in captures.items()}
    assert credits == {
        "2026-11": 414,
        "2026-12": 420,
        "2027-01": 432,
        "2027-02": 384,
        "2027-03": 420,
        "2027-04": 408,
        "2027-05": 432,
        "2027-06": 408,
    }
    assert max(credits.values()) == 432 < 500


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
        # Weekend captures, by the ET date: Saturday 10-31 (EDT) = Korean Sunday morning.
        ("2026-11-01 03:45", ("odds", date(2026, 10, 31), "14:45")),
        ("2026-11-01 04:45", None),
        ("2026-11-01 05:45", ("odds", date(2026, 10, 31), "16:45")),
        ("2026-11-01 06:45", None),
        # Sunday 11-01 (EST from 02:00 that night) = Korean Monday morning, one hour later.
        ("2026-11-02 03:45", None),
        ("2026-11-02 04:45", ("odds", date(2026, 11, 1), "14:45")),
        ("2026-11-02 05:45", None),
        ("2026-11-02 06:45", ("odds", date(2026, 11, 1), "16:45")),
        # ET Monday and ET Friday (a Korean Saturday morning) are weekdays: skip.
        ("2026-11-03 04:45", None),
        ("2026-11-03 06:45", None),
        ("2026-11-07 04:45", None),
        ("2026-11-07 06:45", None),
        # Spring change: Saturday 03-13 still EST, Sunday 03-14 EDT.
        ("2027-03-14 04:45", ("odds", date(2027, 3, 13), "14:45")),
        ("2027-03-14 06:45", ("odds", date(2027, 3, 13), "16:45")),
        ("2027-03-15 03:45", ("odds", date(2027, 3, 14), "14:45")),
        ("2027-03-15 04:45", None),
        ("2027-03-15 05:45", ("odds", date(2027, 3, 14), "16:45")),
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
@pytest.mark.parametrize(
    ("day", "clock"),
    [(date(2026, 11, 5), time(19, 45)), (date(2026, 11, 7), time(14, 45))],
)
def test_odds_tolerance_edges(day: date, clock: time, offset_min: int, runs: bool) -> None:
    """A wake-up counts as a closing slot within +-10 min of its ET time, weekend ones too."""
    at = pl.wall_time(day, clock, EASTERN)
    slot = decide(at + timedelta(minutes=offset_min))
    assert (slot is not None and slot.kind == pl.ODDS) is runs


def test_weekend_marker_name() -> None:
    """Weekend slots use the same marker rule: kind, ET base date, ET clock."""
    assert decide(kst("2026-11-02 04:45")).key == "odds-2026-11-01-1445"
    assert decide(kst("2026-11-01 05:45")).key == "odds-2026-10-31-1645"


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


def test_block_at_1945_resumes_one_slot_early_at_1845_next_day(tmp_path: Path) -> None:
    """Blocked at 19:45 ET (Tue 11-03): the pause is 22 h 50 min, so 18:45 ET on 11-04 runs.

    The behaviour the comment on BLOCK_PAUSE and README describe (P-024 review 3, finding 1).
    """
    wakeups = ["2026-11-04 11:45", "2026-11-04 16:00"]
    later = ["2026-11-05 08:45", "2026-11-05 09:45"]
    assert slots_after_block(tmp_path, "2026-11-04 09:45", wakeups + later) == later


def test_block_at_2145_on_the_autumn_change_resumes_at_1945(tmp_path: Path) -> None:
    """Blocked at 21:45 EDT on Sat 10-31: 25 h day, so 19:45 EST on Sun 11-01 already runs."""
    wakeups = [
        "2026-11-01 16:00",
        "2026-11-02 04:45",  # Sun 14:45 EST
        "2026-11-02 06:45",  # Sun 16:45 EST
        "2026-11-02 08:45",  # Sun 18:45 EST
    ]
    later = ["2026-11-02 09:45", "2026-11-02 11:45"]
    assert slots_after_block(tmp_path, "2026-11-01 10:45", wakeups + later) == later


def test_block_at_saturday_1445_resumes_at_sunday_1445(tmp_path: Path) -> None:
    """The pause applies to weekend slots too: nothing in between, Sunday's 14:45 ET runs."""
    wakeups = [
        "2026-11-08 06:45",  # Sat 16:45 EST
        "2026-11-08 08:45",
        "2026-11-08 09:45",
        "2026-11-08 11:45",
        "2026-11-08 16:00",
    ]
    later = ["2026-11-09 04:45", "2026-11-09 06:45"]
    assert slots_after_block(tmp_path, "2026-11-08 04:45", wakeups + later) == later


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


def test_load_at_1742_after_a_reboot_runs_the_missed_full_run(tmp_path: Path) -> None:
    """RunAtLoad at login 17:42 KST (10-26, powered off over 16:00): the day's full run, once."""
    runner = FakeRunner(0)
    run_at(tmp_path, kst("2026-10-26 17:42"), runner)
    run_at(tmp_path, kst("2026-10-26 18:30"), runner)  # a second login the same evening
    assert runner.calls == [["/venv/bin/presagio", "ingest-daily", "--date", "2026-10-26"]]
    assert "this slot already ran (marker full-2026-10-26-1600)" in read_log(tmp_path, "2026-10-26")


def test_load_at_0916_runs_nothing(tmp_path: Path) -> None:
    """Login 09:16 KST: 19:45 ET was 31 min ago (missed captures are not made up), 16:00 not due."""
    runner = FakeRunner(0)
    run_at(tmp_path, kst("2026-10-26 09:16"), runner)
    assert runner.calls == []
    assert "skip: no slot now (20:16 ET, 09:16 KST)" in read_log(tmp_path, "2026-10-26")


def test_load_at_0750_runs_the_closing_capture_it_falls_in(tmp_path: Path) -> None:
    """Login 07:50 KST (EDT): 5 min after 18:45 ET of Sunday 10-25, within +-10 min: captured."""
    runner = FakeRunner(0)
    run_at(tmp_path, kst("2026-10-26 07:50"), runner)
    assert runner.calls == [
        ["/venv/bin/presagio", "ingest-daily", "--date", "2026-10-25", "--odds-only"]
    ]


def test_rendered_plist_is_valid(tmp_path: Path) -> None:
    """Paths with spaces, '&' and Hangul survive; ten calendar entries; RunAtLoad; no paths."""
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
        (3, 45),
        (4, 45),
        (5, 45),
        (6, 45),
        (7, 45),
        (8, 45),
        (9, 45),
        (10, 45),
        (11, 45),
        (16, 0),
    ]
    assert all(e.keys() == {"Hour", "Minute"} for e in data["StartCalendarInterval"])  # daily
    assert data["RunAtLoad"] is True


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


def test_plan_summary_prints_the_month_table(tmp_path: Path, monkeypatch, capsys) -> None:
    """`plan --summary` is the table in the P-028 PR: days, weekend days, captures, credits."""
    from presagio.config import get_settings

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:1/db")
    monkeypatch.delenv("ODDS_CLOSING_TIMES_ET", raising=False)
    monkeypatch.delenv("INGEST_DAILY_TIME_KST", raising=False)
    get_settings.cache_clear()
    try:
        assert pl.main(["plan", "--from", "2027-05-01", "--days", "31", "--summary"]) == 0
    finally:
        get_settings.cache_clear()
    lines = capsys.readouterr().out.splitlines()
    header = ["month", "days", "sat+sun", "full", "closing", "captures", "credits(max)"]
    assert lines[0].split() == header
    assert lines[1].split() == ["2027-05", "31", "10", "31", "113", "144", "432"]


# --- database check before a run (observation 5) ------------------------------------------------

DB_ADDRESS = ("127.0.0.1", 5433)


class FakeDb:
    """A database port on a fake clock: open from the start, or some seconds after the launch."""

    def __init__(
        self, up: bool = False, opens_after: float | None = None, start_error: OSError | None = None
    ) -> None:
        self.up = up
        self.opens_after = opens_after
        self.start_error = start_error
        self.now = 1000.0
        self.launched_at: float | None = None
        self.starts = 0
        self.sleeps: list[float] = []

    def probe(self, host: str, port: int) -> bool:
        """Open, or opened `opens_after` s after the launch."""
        assert (host, port) == DB_ADDRESS
        if self.up:
            return True
        if self.opens_after is None or self.launched_at is None:
            return False
        return self.now - self.launched_at >= self.opens_after

    def start(self) -> None:
        """Pretend to run `open -a Docker`."""
        self.starts += 1
        if self.start_error is not None:
            raise self.start_error
        self.launched_at = self.now

    def sleep(self, seconds: float) -> None:
        """Advance the fake clock."""
        self.sleeps.append(seconds)
        self.now += seconds

    def clock(self) -> float:
        """The fake monotonic clock."""
        return self.now

    def check(self, start: bool = True) -> object:
        """A DbCheck wired to this fake."""
        return pl.DbCheck(
            DB_ADDRESS,
            probe=self.probe,
            start=self.start if start else None,
            sleep=self.sleep,
            clock=self.clock,
        )


def db_lines(tmp_path: Path, day: str = "2026-10-23") -> list[str]:
    """The wrapper's 'db:' lines in the day's log."""
    return [line for line in read_log(tmp_path, day).splitlines() if " db: " in line]


def test_db_up_starts_nothing_and_logs_nothing(tmp_path: Path) -> None:
    """Port open: no Docker launch, no wait, no extra line; the run goes ahead."""
    db, runner = FakeDb(up=True), FakeRunner(0)
    assert run_at(tmp_path, kst("2026-10-23 16:00"), runner, db=db.check()) == 0
    assert db.starts == 0 and db.sleeps == [] and len(runner.calls) == 1
    assert db_lines(tmp_path) == []


def test_db_down_launches_docker_and_waits(tmp_path: Path) -> None:
    """Port closed: `open -a Docker` once, probes every 2 s, runs when it opens (7 s -> 8 s)."""
    db, runner = FakeDb(opens_after=7), FakeRunner(0)
    assert run_at(tmp_path, kst("2026-10-23 16:00"), runner, db=db.check()) == 0
    assert db.starts == 1 and db.sleeps == [2.0] * 4 and len(runner.calls) == 1
    (line,) = db_lines(tmp_path)
    assert line.endswith("db: down at start; launched Docker Desktop; up after 8 s")
    log = read_log(tmp_path, "2026-10-23")
    assert log.index(" db: ") < log.index(" start full")  # before the child


def test_db_still_down_after_90_s_runs_anyway(tmp_path: Path) -> None:
    """Never opens: one line after 90 s, then the child runs and fails as before (exit 1)."""
    db, runner = FakeDb(), FakeRunner(1)
    assert run_at(tmp_path, kst("2026-10-23 16:00"), runner, db=db.check()) == 1
    assert db.starts == 1 and sum(db.sleeps) == 90 and len(runner.calls) == 1
    (line,) = db_lines(tmp_path)
    assert line.endswith(
        "db: still down after 90 s (Docker Desktop not running?); launched Docker Desktop; "
        "running anyway"
    )
    assert "RESULT exit=1 FAILED" in read_log(tmp_path, "2026-10-23")


def test_docker_launch_error_is_one_line_and_the_child_runs(tmp_path: Path) -> None:
    """`open` missing (not macOS): the error in the same single line; still waits, still runs."""
    error = FileNotFoundError(2, "No such file or directory", "/usr/bin/open")
    db, runner = FakeDb(start_error=error), FakeRunner(1)
    run_at(tmp_path, kst("2026-10-23 16:00"), runner, db=db.check())
    assert db.starts == 1 and len(runner.calls) == 1
    (line,) = db_lines(tmp_path)
    assert "could not launch Docker Desktop (FileNotFoundError: " in line
    assert "still down after 90 s" in line and line.endswith("running anyway")


def test_no_start_docker_waits_without_launching(tmp_path: Path) -> None:
    """--no-start-docker (P-044 on Linux): no launch, but a database coming up is waited for."""
    db, runner = FakeDb(opens_after=2), FakeRunner(0)
    db.launched_at = db.now  # e.g. the Docker daemon is already starting after a boot
    run_at(tmp_path, kst("2026-10-23 16:00"), runner, db=db.check(start=False))
    assert db.starts == 0 and len(runner.calls) == 1
    (line,) = db_lines(tmp_path)
    assert line.endswith(
        "db: down at start; not starting Docker Desktop (--no-start-docker); up after 2 s"
    )


def test_db_check_only_for_slots_that_run(tmp_path: Path) -> None:
    """No slot, an already-run slot and a dry run never probe or launch anything."""
    db = FakeDb()
    run_at(tmp_path, kst("2026-10-24 09:45"), FakeRunner(0), db=db.check())  # no slot
    run_at(tmp_path, kst("2026-10-23 16:00"), FakeRunner(0), db=db.check(), dry_run=True)
    (tmp_path / "state" / "slots").mkdir(parents=True)
    (tmp_path / "state" / "slots" / "full-2026-10-23-1600").write_text("x")
    run_at(tmp_path, kst("2026-10-23 16:30"), FakeRunner(0), db=db.check())  # already ran
    assert db.starts == 0 and db.sleeps == []


def test_unreadable_db_address_is_one_line(tmp_path: Path) -> None:
    """No host/port from DATABASE_URL: say so once and run without waiting."""
    runner = FakeRunner(0)
    run_at(tmp_path, kst("2026-10-23 16:00"), runner, db=pl.DbCheck(None))
    assert len(runner.calls) == 1
    (line,) = db_lines(tmp_path)
    assert line.endswith("db: no host/port readable from DATABASE_URL; not checked")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql+psycopg://u:SECRETPW@127.0.0.1:5433/presagio", ("127.0.0.1", 5433)),
        ("postgresql+psycopg://u:SECRETPW@db.example/presagio", ("db.example", 5432)),
        ("postgresql+psycopg://u:SECRETPW@/presagio", None),  # Unix socket: no TCP port
        ("not a url SECRETPW", None),
    ],
)
def test_read_db_address(monkeypatch, url: str, expected: tuple | None) -> None:
    """Only host and port leave DATABASE_URL; a URL that does not parse gives None."""
    from presagio.config import get_settings

    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    try:
        assert pl.read_db_address() == expected
    finally:
        get_settings.cache_clear()


def test_main_run_passes_the_db_check(tmp_path: Path, monkeypatch) -> None:
    """main(run): DbCheck from DATABASE_URL's host/port; --no-start-docker drops the launch."""
    seen = []
    monkeypatch.setattr(pl, "run", lambda **kwargs: seen.append(kwargs["db"]) or 0)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:SECRETPW@127.0.0.1:5433/db")
    run_main(tmp_path, monkeypatch)
    run_main(tmp_path, monkeypatch, "--no-start-docker")
    assert [d.address for d in seen] == [DB_ADDRESS, DB_ADDRESS]
    assert seen[0].start is pl.start_docker_desktop and seen[1].start is None
    assert seen[0].wait == timedelta(seconds=90)


def test_start_docker_desktop_runs_open_once(monkeypatch) -> None:
    """`/usr/bin/open -a Docker` (stubbed: CI is Linux); a non-zero exit is an OSError."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(tuple(cmd))
        return SimpleNamespace(returncode=len(calls) - 1)

    monkeypatch.setattr(pl.subprocess, "run", fake_run)
    pl.start_docker_desktop()
    with pytest.raises(OSError, match="exited 1"):
        pl.start_docker_desktop()
    assert calls == [("/usr/bin/open", "-a", "Docker")] * 2


def test_port_open_on_loopback() -> None:
    """A listening loopback socket is open; the same port after closing is not."""
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        assert pl.port_open("127.0.0.1", port) is True
    assert pl.port_open("127.0.0.1", port) is False
