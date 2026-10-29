"""launchd wrapper for `presagio ingest-daily` (P-024, D-048): run only at the D-038 times.

launchd's StartCalendarInterval runs on the Mac's local clock (Asia/Seoul, no daylight saving),
but the closing odds captures are pinned to US Eastern wall time (ODDS_CLOSING_TIMES_ET), which
moves by an hour against Korea twice a year. So the job wakes at every Korean time a slot can
fall on (summer and winter), and this wrapper decides from the real clock whether "now" is a
slot. Of the nine closing-capture wake-ups of an ET day, six are not one on a weekday and four
on a Saturday or Sunday; they end quietly with a "skip" log line.

A weekday therefore has exactly four `presagio ingest-daily` invocations: the full run at
INGEST_DAILY_TIME_KST and one `--odds-only` run per ODDS_CLOSING_TIMES_ET entry. Saturdays and
Sundays (the slot's ET date) add the two WEEKEND_CLOSING_ET captures: six (P-028, D-027 as
amended by D-049 M-9). A marker file per slot makes a second wake-up for the same slot (manual
kickstart, coalesced wake, the RunAtLoad wake at login) a no-op.

Before a slot's run the wrapper checks that DATABASE_URL answers a query; if not, it starts
Docker Desktop once (local database only) and waits up to DB_WAIT for it (P-028).

On the P-044 collector VM a systemd timer calls `run --odds-only-host --no-start-docker` at the
closing-capture slots only; the full run stays on the Mac (D-058).

Subcommands (run from the repository with `uv run python <this file> ...`):
  run       what launchd calls: decide, run ingest-daily, append everything to the day's log
  calendar  print the Korean wake-up times the plist needs
  render    write the plist from the template (install.sh)
  plan      print the decision for every wake-up over a date range (dry run, no command run)

Exit codes of `run` are the ones of ingest-daily (D-037): 0 ok or skipped, 1 failed/partial,
2 blocked (HTTP 403/429), 64 bad arguments; 124 when the wrapper killed a run that took too long.
The child is the venv's own `presagio` console script (not `uv run`), so an exit code 2 can only
come from ingest-daily, and it is only read as a block when ingest-daily printed its block line.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time as monotonic_time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, TextIO
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

SEOUL = ZoneInfo("Asia/Seoul")
EASTERN = ZoneInfo("America/New_York")
LABEL = "com.presagio.ingest-daily"
FULL = "full"
ODDS = "odds"
# How far from a closing-capture slot a wake-up may be and still count as that slot. launchd
# fires on the minute while the Mac is awake; a wake-up after sleep comes late (coalesced), and
# a capture much later than 15 min before tip-off is no longer a pre-tip price, so it is skipped.
SLOT_TOLERANCE = timedelta(minutes=10)
# D-027 as amended (D-049 M-9): on Saturdays and Sundays (the slot's ET date) two more closing
# captures for the afternoon games, whose tip-off is before the first ODDS_CLOSING_TIMES_ET slot.
# They extend the closing captures: an empty ODDS_CLOSING_TIMES_ET turns these off too.
WEEKEND_CLOSING_ET = (time(14, 45), time(16, 45))
WEEKEND = (5, 6)  # date.weekday() of Saturday and Sunday
# After exit code 2 (blocked, D-037) every slot scheduled less than this after the blocked slot's
# scheduled time is skipped. Slot times are compared, not wall clocks, so seconds of start-up
# jitter never decide it. The pause ends 1 h 10 min before the next day's same slot (on a 24 h
# day), and the first slot after it is (every slot of 2026-10..2027-05 blocked once in `plan`):
#   - the next day's same slot: the full run, 18:45 and 21:45 ET, Saturday 14:45 and 16:45 ET;
#   - one slot early: 19:45 ET resumes at 18:45 ET the next day;
#   - Sunday 14:45 and 16:45 ET resume at Monday 18:45 ET (Monday has neither slot);
#   - blocked on the Saturday before the autumn change (10-31; Sunday is a 25 h day), 16:45,
#     18:45 and 21:45 ET also resume one slot early, at Sunday 14:45, 16:45 and 19:45 ET;
#   - blocked on the Saturday before the spring change (03-13; Sunday is a 23 h day), every
#     slot, 19:45 ET included, resumes at Sunday's same slot: that is why it is 23 h, not 24 h.
# An early resume loses nothing; that day only gets one more closing snapshot. Delete the file
# to resume earlier.
BLOCK_PAUSE = timedelta(hours=23) - SLOT_TOLERANCE
BLOCKED_FILE = "blocked"
SLOTS_DIR = "slots"
# Slot markers older than this are deleted on each run.
MARKER_KEEP = timedelta(days=14)
# Days whose wall-clock offset ET -> KST differ: one in winter (EST), one in summer (EDT).
SAMPLE_DAYS = (date(2027, 1, 15), date(2027, 7, 15))
# A normal run takes about a minute; one this long is stuck (e.g. waiting on a database lock) and
# would make launchd drop every later wake-up while it runs.
RUN_TIMEOUT = timedelta(minutes=30)
# Docker Desktop does not start by itself after a reboot unless its "Start Docker Desktop when you
# sign in" setting is on (2026-10-25/26: the daemon answered about 5 s after `open -a Docker`, the
# database a few seconds later). The wait comes before the run and is separate from RUN_TIMEOUT.
# Ready means a psycopg connection runs SELECT 1, not an open port: Docker Desktop's port proxy
# accepts connections before Postgres does (P-028 review 1, finding 1).
DOCKER_START = ("/usr/bin/open", "-a", "Docker")
DB_WAIT = timedelta(seconds=90)
DB_POLL = timedelta(seconds=2)
DB_CONNECT_TIMEOUT = 2  # seconds per connection attempt (libpq's smallest connect_timeout)
# Docker Desktop on this Mac can only serve a database on these hosts (not the P-044 VM).
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
# The Odds API cost of one capture: 3 markets x 1 region (measured 10-25 479 -> 476, 10-26 -> 473).
CREDITS_PER_CAPTURE = 3
EXIT_OK, EXIT_FAILED, EXIT_BLOCKED, EXIT_USAGE, EXIT_TIMEOUT = 0, 1, 2, 64, 124
EXIT_MEANING = {
    EXIT_OK: "OK",
    EXIT_FAILED: "FAILED (a unit failed or ended partial; see the report above)",
    EXIT_BLOCKED: "BLOCKED (HTTP 403/429): tell a human. Slots of the next 22 h 50 min skip",
    EXIT_USAGE: "USAGE (bad arguments: a wrapper bug)",
    EXIT_TIMEOUT: f"TIMEOUT (killed after {RUN_TIMEOUT.seconds // 60} min)",
}
# What ingest-daily prints when it stops on a block (src/presagio/ingestion/incremental.py).
BLOCK_LINE = "Stopped: the source blocked us"
TAG = "[presagio-launchd]"
# --odds-only-host (P-044, D-058): the collector VM runs closing captures only. stats.nba.com
# answers its data-centre address with 403 (measured 2026-10-28), so a full run there would exit 2
# and the block pause (BLOCK_PAUSE) would then skip the next day's closing captures as well.
NOT_THIS_HOST = "skip: full run is not this host's job (--odds-only-host)"

Runner = Callable[..., Any]


@dataclass(frozen=True)
class Slot:
    """One planned ingest-daily invocation: kind, base date D (the --date value) and its time."""

    kind: str  # FULL or ODDS
    base_date: date
    at: datetime  # aware, UTC

    @property
    def key(self) -> str:
        """Marker name: 'odds-2026-10-23-1845' (ET clock) or 'full-2026-10-23-1600' (KST)."""
        zone = EASTERN if self.kind == ODDS else SEOUL
        return f"{self.kind}-{self.base_date.isoformat()}-{self.at.astimezone(zone):%H%M}"

    def describe(self) -> str:
        """'odds base_date=2026-10-23 at 18:45 ET (10-24 07:45 KST)'."""
        return (
            f"{self.kind} base_date={self.base_date.isoformat()} at "
            f"{self.at.astimezone(EASTERN):%m-%d %H:%M} ET "
            f"({self.at.astimezone(SEOUL):%m-%d %H:%M} KST)"
        )


def parse_clock(value: str) -> time:
    """'18:45' -> time(18, 45)."""
    hours, minutes = (int(part) for part in value.strip().split(":"))
    return time(hours, minutes)


def wall_time(day: date, clock: time, zone: ZoneInfo) -> datetime:
    """UTC moment of a wall-clock time on a day in a zone."""
    return datetime.combine(day, clock, zone).astimezone(UTC)


def full_slot_for(now: datetime, full_kst: time) -> Slot | None:
    """The full run if now is between its Korean time (minus tolerance) and Korean midnight.

    A wake-up after sleep still runs the day's full run late (results, today's schedule); once
    the Korean date has changed it is gone and the next day's run retries D-1 (D-039 window).
    """
    today = now.astimezone(SEOUL).date()
    at = wall_time(today, full_kst, SEOUL)
    if at - SLOT_TOLERANCE <= now < wall_time(today + timedelta(days=1), time(0), SEOUL):
        return Slot(FULL, today, at)
    return None


def closing_times_on(day: date, closing_et: Sequence[time]) -> tuple[time, ...]:
    """Closing captures of an ET date: closing_et, plus WEEKEND_CLOSING_ET on Saturday and Sunday.

    The weekend ones are added only when closing_et is not empty (no closing captures at all).
    """
    if closing_et and day.weekday() in WEEKEND:
        return tuple(sorted({*closing_et, *WEEKEND_CLOSING_ET}))
    return tuple(closing_et)


def odds_slot_for(now: datetime, closing_et: Sequence[time]) -> Slot | None:
    """The closing capture whose Eastern time is within SLOT_TOLERANCE of now, if any.

    The base date is the slot's ET date: ingest-daily plans the closing captures of base date D
    on ET date D (presagio.ingestion.incremental.capture_times). Whether the weekend captures
    apply is decided by that ET date, not by the Korean date of the wake-up.
    """
    et_today = now.astimezone(EASTERN).date()
    for day in (et_today - timedelta(days=1), et_today, et_today + timedelta(days=1)):
        for clock in closing_times_on(day, closing_et):
            at = wall_time(day, clock, EASTERN)
            if abs(now - at) <= SLOT_TOLERANCE:
                return Slot(ODDS, day, at)
    return None


def decide(now: datetime, full_kst: time, closing_et: Sequence[time]) -> Slot | None:
    """The slot this wake-up stands for, or None (not a slot: skip quietly)."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return odds_slot_for(now, closing_et) or full_slot_for(now, full_kst)


def calendar_entries(full_kst: time, closing_et: Sequence[time]) -> list[tuple[int, int]]:
    """Korean (hour, minute) wake-ups covering every slot in both EST and EDT, sorted.

    The weekend captures' wake-ups are on every day (one plist entry each); on weekdays the
    wrapper skips them.
    """
    entries = {(full_kst.hour, full_kst.minute)}
    weekend = WEEKEND_CLOSING_ET if closing_et else ()
    for clock in {*closing_et, *weekend}:
        for day in SAMPLE_DAYS:
            local = wall_time(day, clock, EASTERN).astimezone(SEOUL)
            entries.add((local.hour, local.minute))
    return sorted(entries)


def presagio_script() -> str:
    """The `presagio` console script of the venv this wrapper runs in (uv run --project REPO)."""
    return str(Path(sys.executable).parent / "presagio")


def command_for(slot: Slot, presagio: str, dry_run: bool = False) -> list[str]:
    """The ingest-daily command line for a slot, with an explicit --date."""
    cmd = [presagio, "ingest-daily", "--date", slot.base_date.isoformat()]
    if slot.kind == ODDS:
        cmd.append("--odds-only")
    if dry_run:
        cmd.append("--dry-run")
    return cmd


# --- state: slot markers and the block pause ----------------------------------------------------


def claim_slot(state_dir: Path, slot: Slot) -> bool:
    """Create the slot's marker atomically; False if this slot already ran (or is running)."""
    folder = state_dir / SLOTS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(folder / slot.key, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as marker:
        marker.write(f"{datetime.now(UTC).isoformat()} pid={os.getpid()}\n")
    return True


def prune_markers(state_dir: Path, now: datetime) -> None:
    """Delete slot markers older than MARKER_KEEP."""
    folder = state_dir / SLOTS_DIR
    if not folder.is_dir():
        return
    cutoff = (now - MARKER_KEEP).timestamp()
    for marker in folder.iterdir():
        if marker.is_file() and marker.stat().st_mtime < cutoff:
            marker.unlink()


def blocked_until(state_dir: Path) -> datetime | None:
    """First slot time that runs again after an exit code 2, or None without a pause file.

    The file holds the blocked slot's scheduled time; an unreadable file counts from its mtime.
    """
    path = state_dir / BLOCKED_FILE
    if not path.is_file():
        return None
    try:
        blocked_at = datetime.fromisoformat(path.read_text().split()[0])
        if blocked_at.tzinfo is None:
            raise ValueError("naive time")
    except (IndexError, ValueError):
        blocked_at = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    return blocked_at + BLOCK_PAUSE


def record_block(state_dir: Path, now: datetime, slot: Slot) -> None:
    """Start the pause: the blocked slot's scheduled time, its key and when it happened."""
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / BLOCKED_FILE).write_text(f"{slot.at.isoformat()} {slot.key} {now.isoformat()}\n")


# --- database: wait for it, start Docker Desktop once ---------------------------------------------


def connect_probe(database_url: str) -> Callable[[], bool]:
    """A probe that is True when DATABASE_URL accepts a psycopg connection and runs SELECT 1.

    The SQLAlchemy URL ('postgresql+psycopg://...') loses its driver suffix for libpq. Every
    error is just False: messages can hold the URL or password, so nothing of them is kept.
    """
    import psycopg
    from sqlalchemy.engine import make_url

    conninfo = make_url(database_url).set(drivername="postgresql")
    target = conninfo.render_as_string(hide_password=False)

    def probe() -> bool:
        """One connection attempt within DB_CONNECT_TIMEOUT."""
        try:
            with psycopg.connect(target, connect_timeout=DB_CONNECT_TIMEOUT) as conn:
                conn.execute("SELECT 1").fetchone()
        except Exception:  # not ready (refused, reset, starting up, timeout) or unusable
            return False
        return True

    return probe


def start_docker_desktop() -> None:
    """Run DOCKER_START (macOS `open -a Docker`); OSError if it cannot run or exits non-zero."""
    try:
        result = subprocess.run(DOCKER_START, capture_output=True, timeout=30, check=False)
    except subprocess.TimeoutExpired as exc:
        raise OSError(f"{DOCKER_START[0]} did not return within 30 s") from exc
    if result.returncode != 0:
        raise OSError(f"{' '.join(DOCKER_START)} exited {result.returncode}")


@dataclass(frozen=True)
class DbCheck:
    """How run() checks the database before a slot's run (every part injectable for tests)."""

    probe: Callable[[], bool] | None  # connect_probe(DATABASE_URL); None if not readable
    local: bool = True  # the host is a loopback address, so Docker Desktop here can serve it
    start: Callable[[], None] | None = start_docker_desktop  # None: --no-start-docker
    sleep: Callable[[float], None] = monotonic_time.sleep
    clock: Callable[[], float] = monotonic_time.monotonic
    wait: timedelta = DB_WAIT


def wait_for_db(check: DbCheck, say: Callable[[str], None]) -> None:
    """If the database does not answer, start Docker Desktop once and wait for it; one log line.

    Nothing is logged when it answers at once. Docker Desktop is started only for a loopback
    host. The run goes ahead either way: a database that is still down fails it as before
    (connection refused, exit 1). No host, port, URL or error message is logged.
    """
    if check.probe is None:
        say("db: DATABASE_URL not readable; not checked")
        return
    if check.probe():
        return
    if check.start is None:
        action = "not starting Docker Desktop (--no-start-docker)"
    elif not check.local:
        action = "remote host, not starting Docker Desktop"
    else:
        try:
            check.start()
            action = "launched Docker Desktop"
        except OSError as exc:
            action = f"could not launch Docker Desktop ({type(exc).__name__}: {exc})"
    started = check.clock()
    limit = check.wait.total_seconds()
    while check.clock() - started < limit:
        check.sleep(DB_POLL.total_seconds())
        if check.probe():
            say(f"db: down at start; {action}; up after {check.clock() - started:.0f} s")
            return
    say(
        f"db: still down after {limit:.0f} s (Docker Desktop not running?); {action}; "
        "running anyway"
    )


# --- run ----------------------------------------------------------------------------------------


def log_path(log_dir: Path, now: datetime) -> Path:
    """One log file per Korean date: ingest-2026-10-23.log."""
    return log_dir / f"ingest-{now.astimezone(SEOUL).date().isoformat()}.log"


def stamp(now: datetime | None = None) -> str:
    """Korean ISO timestamp for log lines."""
    return (now or datetime.now(UTC)).astimezone(SEOUL).isoformat(timespec="seconds")


def read_times() -> tuple[time, tuple[time, ...]]:
    """INGEST_DAILY_TIME_KST and ODDS_CLOSING_TIMES_ET from presagio.config (reads .env in cwd)."""
    from presagio.config import get_settings, parse_clock_times

    settings = get_settings()
    closing = tuple(parse_clock(t) for t in parse_clock_times(settings.odds_closing_times_et))
    return parse_clock(settings.ingest_daily_time_kst), closing


def read_db_check(start_docker: bool) -> DbCheck:
    """DbCheck for DATABASE_URL: a connect probe and whether its host is a loopback address."""
    from sqlalchemy.engine import make_url

    from presagio.config import get_settings

    database_url = get_settings().database_url
    try:
        host = make_url(database_url).host
        probe = connect_probe(database_url)
    except Exception:  # sqlalchemy's ArgumentError; the message would echo the URL
        return DbCheck(None)
    return DbCheck(
        probe,
        local=host in LOOPBACK_HOSTS,
        start=start_docker_desktop if start_docker else None,
    )


def settings_error(exc: Exception) -> str:
    """Error type and failing setting names only: messages could echo values from .env."""
    errors = getattr(exc, "errors", None)
    fields = sorted({str(e["loc"][0]) for e in errors() if e.get("loc")}) if errors else []
    return f"{type(exc).__name__}" + (f" in {', '.join(fields)}" if fields else "")


def run(
    *,
    now: datetime,
    full_kst: time,
    closing_et: Sequence[time],
    repo: Path,
    presagio: str,
    log_dir: Path,
    state_dir: Path,
    dry_run: bool = False,
    runner: Runner = subprocess.run,
    db: DbCheck | None = None,
    odds_only_host: bool = False,
) -> int:
    """Decide the slot for now and, if it is one, run ingest-daily once into the day's log.

    With db, a real (not dry) run first waits for the database (wait_for_db). With
    odds_only_host, a full-run slot ends in one NOT_THIS_HOST line: no marker, no call, exit 0.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    with log_path(log_dir, now).open("a", encoding="utf-8") as log:

        def say(message: str) -> None:
            """One wrapper line in the log."""
            log.write(f"{stamp()} {TAG} {message}\n")
            log.flush()

        slot = decide(now, full_kst, closing_et)
        if slot is None:
            # The reinstall hint is about launchd's wake-ups; the VM's timer is not rendered.
            note = "" if odds_only_host else off_calendar_note(now, full_kst, closing_et)
            say(
                f"skip: no slot now ({now.astimezone(EASTERN):%H:%M} ET, "
                f"{now.astimezone(SEOUL):%H:%M} KST){note}"
            )
            return EXIT_OK
        if odds_only_host and slot.kind == FULL:
            say(f"{NOT_THIS_HOST}: {slot.describe()}")
            return EXIT_OK
        if not dry_run:
            prune_markers(state_dir, now)
            until = blocked_until(state_dir)
            if until is not None and slot.at < until:
                say(
                    f"skip {slot.describe()}: paused after a block; slots from {stamp(until)} run "
                    f"(delete {state_dir / BLOCKED_FILE} to resume)"
                )
                return EXIT_OK
            if not claim_slot(state_dir, slot):
                say(f"skip {slot.describe()}: this slot already ran (marker {slot.key})")
                return EXIT_OK
            if db is not None:
                wait_for_db(db, say)
        cmd = command_for(slot, presagio, dry_run)
        say(f"start {slot.describe()}: presagio {' '.join(cmd[1:])} (in {repo})")
        return execute(cmd, slot, now, repo, log, say, state_dir, dry_run, runner)


def execute(
    cmd: list[str],
    slot: Slot,
    now: datetime,
    repo: Path,
    log: TextIO,
    say: Callable[[str], None],
    state_dir: Path,
    dry_run: bool,
    runner: Runner,
) -> int:
    """Run the command with stdout and stderr appended to the log; log a RESULT line."""
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    started = monotonic_time.monotonic()
    log.flush()
    output_from = log.tell()
    try:
        code = runner(
            cmd,
            cwd=repo,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
            timeout=RUN_TIMEOUT.total_seconds(),
        ).returncode
    except subprocess.TimeoutExpired:  # subprocess.run has killed the child
        code = EXIT_TIMEOUT
    except OSError as exc:  # script missing or repo gone: still one RESULT line
        log.flush()
        say(f"cannot start {cmd[0]}: {exc}")
        code = EXIT_FAILED
    log.flush()
    meaning = EXIT_MEANING.get(code, "UNEXPECTED exit code")
    blocked = code == EXIT_BLOCKED and BLOCK_LINE in read_from(Path(log.name), output_from)
    if code == EXIT_BLOCKED and not blocked:
        meaning = f"UNEXPECTED (exit 2 without ingest-daily's '{BLOCK_LINE}' line; no pause)"
    say(f"RESULT exit={code} {meaning} [{slot.key}] {monotonic_time.monotonic() - started:.1f} s")
    if blocked and not dry_run:
        record_block(state_dir, now, slot)
    return code


def read_from(path: Path, offset: int) -> str:
    """The log text written after a byte offset (this run's output)."""
    with path.open("rb") as log:
        log.seek(offset)
        return log.read().decode("utf-8", errors="replace")


def off_calendar_note(now: datetime, full_kst: time, closing_et: Sequence[time]) -> str:
    """A hint when a skip happens away from every wake-up the current settings would schedule.

    launchd's wake-ups are computed at install time; if .env times changed since, every wake-up
    skips until install.sh is run again. Manual kickstarts at other times also show the hint.
    """
    local = now.astimezone(SEOUL)
    for hour, minute in calendar_entries(full_kst, closing_et):
        if abs(local - local.replace(hour=hour, minute=minute, second=0, microsecond=0)) <= (
            SLOT_TOLERANCE
        ):
            return ""
    return (
        "; not a wake-up time of the current settings: if INGEST_DAILY_TIME_KST or "
        "ODDS_CLOSING_TIMES_ET changed, run scripts/launchd/install.sh again"
    )


# --- plist ----------------------------------------------------------------------------------------


def render_plist(
    template: str,
    *,
    repo: Path,
    uv: str,
    wrapper: Path,
    log_dir: Path,
    state_dir: Path,
    entries: Sequence[tuple[int, int]],
) -> str:
    """Fill the template's @PLACEHOLDERS@ (XML-escaped) and the calendar entries."""
    calendar = "\n".join(
        f"        <dict><key>Hour</key><integer>{h}</integer>"
        f"<key>Minute</key><integer>{m}</integer></dict>"
        for h, m in entries
    )
    values = {
        "@LABEL@": LABEL,
        "@REPO@": str(repo),
        "@UV@": uv,
        "@UV_DIR@": str(Path(uv).parent),
        "@WRAPPER@": str(wrapper),
        "@LOG_DIR@": str(log_dir),
        "@STATE_DIR@": str(state_dir),
        "@HOME@": str(Path.home()),
    }
    out = template
    for name, value in values.items():
        out = out.replace(name, escape(value))
    return out.replace("@CALENDAR@", calendar)


def wakeups(
    start: date, days: int, full_kst: time, closing_et: Sequence[time]
) -> list[tuple[datetime, Slot | None]]:
    """Every Korean wake-up of N days from start and the slot it stands for (None: skip)."""
    entries = calendar_entries(full_kst, closing_et)
    out = []
    for n in range(days):
        day = start + timedelta(days=n)
        for hour, minute in entries:
            now = wall_time(day, time(hour, minute), SEOUL)
            out.append((now, decide(now, full_kst, closing_et)))
    return out


def print_plan(start: date, days: int, full_kst: time, closing_et: Sequence[time]) -> None:
    """Every Korean wake-up from start for N days and what the wrapper would do."""
    for now, slot in wakeups(start, days, full_kst, closing_et):
        what = slot.describe() if slot else "skip"
        print(
            f"{now.astimezone(SEOUL):%Y-%m-%d %H:%M} KST = "
            f"{now.astimezone(EASTERN):%m-%d %H:%M %Z}  ->  {what}"
        )


def month_totals(
    start: date, days: int, full_kst: time, closing_et: Sequence[time]
) -> dict[str, Counter[str]]:
    """Per month of the base date: days, weekend days, full runs and closing captures.

    Base dates start .. start+days-1; one more Korean day is simulated because the last ET
    date's closing captures fall on the next Korean morning.
    """
    end = start + timedelta(days=days)
    months: dict[str, Counter[str]] = {}
    for n in range(days):
        day = start + timedelta(days=n)
        month = months.setdefault(f"{day:%Y-%m}", Counter())
        month["days"] += 1
        month["weekend_days"] += day.weekday() in WEEKEND
    for _, slot in wakeups(start, days + 1, full_kst, closing_et):
        if slot is not None and start <= slot.base_date < end:
            months[f"{slot.base_date:%Y-%m}"][slot.kind] += 1
    return months


def print_summary(start: date, days: int, full_kst: time, closing_et: Sequence[time]) -> None:
    """month_totals as a table, with the most credits the captures can cost."""
    print("month    days  sat+sun  full  closing  captures  credits(max)")
    for month, n in month_totals(start, days, full_kst, closing_et).items():
        captures = n[FULL] + n[ODDS]  # the full run captures odds once ('prediction')
        print(
            f"{month}  {n['days']:4d}  {n['weekend_days']:7d}  {n[FULL]:4d}  {n[ODDS]:7d}  "
            f"{captures:8d}  {captures * CREDITS_PER_CAPTURE:12d}"
        )


def build_parser() -> argparse.ArgumentParser:
    """CLI for launchd (run) and install.sh (calendar, render, plan)."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    run_cmd = commands.add_parser("run", help="what launchd calls")
    run_cmd.add_argument("--repo", type=Path, required=True)
    run_cmd.add_argument("--uv", help="ignored; accepted so plists written before 2026-10-23 work")
    run_cmd.add_argument("--log-dir", type=Path, required=True)
    run_cmd.add_argument("--state-dir", type=Path, required=True)
    run_cmd.add_argument(
        "--now",
        type=datetime.fromisoformat,
        help="pretend it is this aware time (testing; needs --dry-run)",
    )
    run_cmd.add_argument(
        "--dry-run", action="store_true", help="pass --dry-run to ingest-daily, keep no markers"
    )
    run_cmd.add_argument(
        "--no-start-docker",
        action="store_true",
        help="if the database is down, wait for it but do not run `open -a Docker` (not macOS)",
    )
    run_cmd.add_argument(
        "--odds-only-host",
        action="store_true",
        help="never run the full run here, only closing captures (the P-044 collector VM)",
    )
    commands.add_parser("calendar", help="print the Korean wake-up times")
    render = commands.add_parser("render", help="print the plist")
    render.add_argument("--template", type=Path, required=True)
    render.add_argument("--repo", type=Path, required=True)
    render.add_argument("--uv", required=True)
    render.add_argument("--wrapper", type=Path, required=True, help="REPO/scripts/launchd/...")
    render.add_argument("--log-dir", type=Path, required=True)
    render.add_argument("--state-dir", type=Path, required=True)
    plan = commands.add_parser("plan", help="print the decision for each wake-up")
    plan.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    plan.add_argument("--days", type=int, default=3)
    plan.add_argument(
        "--summary", action="store_true", help="per-month captures and credits, not each wake-up"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        if args.now is not None and not args.dry_run:
            parser.error("--now needs --dry-run (it would write slot markers for a fake time)")
        now = args.now or datetime.now(UTC)
        if now.tzinfo is None:
            raise SystemExit("--now needs a UTC offset, e.g. 2026-11-02T08:45+09:00")
        try:
            full_kst, closing_et = read_times()
        except Exception as exc:  # bad or missing .env: still one RESULT line in the day's log
            args.log_dir.mkdir(parents=True, exist_ok=True)
            with log_path(args.log_dir, now).open("a", encoding="utf-8") as log:
                log.write(
                    f"{stamp()} {TAG} RESULT exit={EXIT_FAILED} FAILED "
                    f"(settings: {settings_error(exc)})\n"
                )
            return EXIT_FAILED
        return run(
            now=now,
            full_kst=full_kst,
            closing_et=closing_et,
            repo=args.repo,
            presagio=presagio_script(),
            log_dir=args.log_dir,
            state_dir=args.state_dir,
            dry_run=args.dry_run,
            db=read_db_check(start_docker=not args.no_start_docker),
            odds_only_host=args.odds_only_host,
        )
    full_kst, closing_et = read_times()
    if args.command == "calendar":
        for hour, minute in calendar_entries(full_kst, closing_et):
            print(f"{hour:02d}:{minute:02d}")
        return EXIT_OK
    if args.command == "render":
        print(
            render_plist(
                args.template.read_text(encoding="utf-8"),
                repo=args.repo,
                uv=args.uv,
                wrapper=args.wrapper,
                log_dir=args.log_dir,
                state_dir=args.state_dir,
                entries=calendar_entries(full_kst, closing_et),
            ),
            end="",
        )
        return EXIT_OK
    if args.summary:
        print_summary(args.start, args.days, full_kst, closing_et)
    else:
        print_plan(args.start, args.days, full_kst, closing_et)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
