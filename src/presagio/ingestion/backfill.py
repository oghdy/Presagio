"""Backfill: seasons or a date range -> a plan of work units -> load them in order, resumably.

Work units (D-023): nba_api's natural unit is the season, not the date. One unit is one call:
  teams                                  CommonTeamYears (once, before any games)
  schedule  season=<S>                   ScheduleLeagueV2, the whole season
  gamelog   season=<S>;season_type=<T>   LeagueGameLog of one season type (games + team stats)
A date range is widened to the seasons it touches and loads those seasons whole, under the same
scope keys, so a later `--season` run finds them done. Calls are never made per date (risk B1).

Resume: a unit is skipped when it already has a 'succeeded' run that started after its season
was over (teams: any 'succeeded' run). partial, failed and aborted units run again, and so does
every unit of a season that is not over yet (its data still changes). --force runs everything.
All loads are upserts, so running a unit again is idempotent.

Failures: a unit that ends partial or failed is reported and the next unit runs. A unit whose
prerequisite failed and left no rows (teams, or the season's games) is not run. A 403/429
(BlockedError) stops the whole backfill at once (D-024, D-032); the parent run ends 'aborted'.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, func, select

from presagio.db.models import Game, IngestionRun, Team
from presagio.ingestion.client import IngestionClient
from presagio.ingestion.errors import BlockedError
from presagio.ingestion.loaders.games import JOB_GAMELOG, JOB_SCHEDULE, load_season_schedule
from presagio.ingestion.loaders.team_stats import load_season_game_log
from presagio.ingestion.loaders.teams import JOB_TEAMS, load_teams
from presagio.ingestion.runs import (
    PARTIAL,
    SUCCEEDED,
    error_line,
    has_succeeded,
    track_run,
)
from presagio.ingestion.sources.base import (
    RESULT_SEASON_TYPES,
    ScheduleSource,
    TeamSource,
    TeamStatsSource,
)
from presagio.ingestion.sources.nba_api import NbaApiScheduleSource, NbaApiTeamSource
from presagio.ingestion.sources.nba_api.team_stats import NbaApiTeamStatsSource

logger = logging.getLogger(__name__)

JOB_BACKFILL = "backfill"
SEASON_TYPES = RESULT_SEASON_TYPES  # ("regular", "playin", "playoffs")
SEASON_FORMAT = re.compile(r"^(\d{4})-(\d{2})$")
EASTERN = ZoneInfo("America/New_York")
# A season's window runs from Oct 15 of its first year to Oct 15 of the next. Regular seasons
# open after Oct 15 (earliest recent opener: 2018-10-16), and finals end before it: normally
# in June, 2019-20 on 2020-10-11 (bubble), 2020-21 on 2021-07-20. A season is over at the end
# of its window; until then its schedule and game logs still change.
SEASON_BOUNDARY = (10, 15)

# Plan actions and the extra unit outcomes besides the run statuses.
RUN = "run"
SKIP = "skip"
SKIPPED = "skipped"  # already done (resume)
NOT_RUN = "not_run"  # prerequisite failed and left no rows
NOT_STARTED = "not_started"  # the run row could not be opened (e.g. already running)

# Assumed response time of one call (s), docs/research/nba_api.md ch. 4: the 4.4 MB schedule
# took up to 2.35 s, game logs and other stats calls up to 1.4 s, small calls about 0.3 s.
ASSUMED_RESPONSE_SECONDS = {JOB_TEAMS: 0.5, JOB_SCHEDULE: 2.5, JOB_GAMELOG: 1.5}
ENDPOINTS = {JOB_TEAMS: "commonteamyears", JOB_SCHEDULE: "scheduleleaguev2"}

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_BLOCKED = 2

RUNS = IngestionRun.__table__
GAMES = Game.__table__
TEAMS = Team.__table__


def parse_season(value: str) -> str:
    """Validate a season like '2023-24' (second part = first year + 1) and return it."""
    match = SEASON_FORMAT.match(value)
    if match is None or int(match[2]) != (int(match[1]) + 1) % 100:
        raise ValueError(f"not a season: {value!r}; expected e.g. 2023-24")
    return value


def season_start_year(season: str) -> int:
    """First calendar year of a season ('2023-24' -> 2023)."""
    return int(parse_season(season)[:4])


def season_name(start_year: int) -> str:
    """Season label for its first calendar year (2023 -> '2023-24')."""
    return f"{start_year}-{(start_year + 1) % 100:02d}"


def season_of_date(day: date) -> str:
    """The season whose window (see SEASON_BOUNDARY) contains the day."""
    month, dom = SEASON_BOUNDARY
    return season_name(day.year if (day.month, day.day) >= (month, dom) else day.year - 1)


def seasons_for_range(start: date, end: date) -> list[str]:
    """Every season whose window overlaps the inclusive date range, oldest first."""
    if end < start:
        raise ValueError(f"--to {end} is before --from {start}")
    first, last = season_start_year(season_of_date(start)), season_start_year(season_of_date(end))
    return [season_name(year) for year in range(first, last + 1)]


def season_over_at(season: str) -> datetime:
    """Moment (UTC) from which the season counts as over and its data as final."""
    month, dom = SEASON_BOUNDARY
    return datetime(season_start_year(season) + 1, month, dom, tzinfo=UTC)


def et_day_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    """UTC interval [start 00:00 ET, end+1 00:00 ET) for an inclusive ET date range (1-2.4)."""
    lower = datetime.combine(start, datetime.min.time(), EASTERN).astimezone(UTC)
    upper = datetime.combine(end + timedelta(days=1), datetime.min.time(), EASTERN)
    return lower, upper.astimezone(UTC)


@dataclass(frozen=True)
class Unit:
    """One work unit = one ingestion_runs child row = one API call."""

    job_type: str
    scope_key: str
    season: str | None = None
    season_type: str | None = None  # ours: regular / playin / playoffs

    @property
    def endpoint(self) -> str:
        """nba_api endpoint the unit calls."""
        return ENDPOINTS.get(self.job_type, "leaguegamelog")

    @property
    def calls(self) -> int:
        """External calls the unit makes when nothing is retried."""
        return 1


@dataclass(frozen=True)
class PlannedUnit:
    """A unit with what this backfill will do about it and why."""

    unit: Unit
    action: str  # RUN or SKIP
    reason: str


@dataclass(frozen=True)
class BackfillRequest:
    """What the user asked for: seasons, or a date range widened to seasons."""

    seasons: tuple[str, ...]
    season_types: tuple[str, ...] = SEASON_TYPES
    date_from: date | None = None
    date_to: date | None = None
    force: bool = False

    @classmethod
    def for_seasons(
        cls, seasons: Iterable[str], season_types: Iterable[str] = SEASON_TYPES, **kw: Any
    ) -> BackfillRequest:
        """Request for explicit seasons (deduplicated, oldest first)."""
        unique = sorted({parse_season(s) for s in seasons}, key=season_start_year)
        if not unique:
            raise ValueError("at least one season is required")
        return cls(tuple(unique), normalize_season_types(season_types), **kw)

    @classmethod
    def for_range(
        cls, start: date, end: date, season_types: Iterable[str] = SEASON_TYPES, **kw: Any
    ) -> BackfillRequest:
        """Request for an inclusive ET date range: the seasons it touches, loaded whole."""
        seasons = tuple(seasons_for_range(start, end))
        return cls(seasons, normalize_season_types(season_types), start, end, **kw)

    def scope_key(self) -> str:
        """Parent run scope_key: the arguments, so the same backfill never runs twice at once."""
        parts = []
        if self.date_from is not None:
            parts += [f"from={self.date_from.isoformat()}", f"to={self.date_to}"]
        parts += [
            f"seasons={','.join(self.seasons)}",
            f"season_types={','.join(self.season_types)}",
        ]
        return ";".join(parts)


def normalize_season_types(season_types: Iterable[str]) -> tuple[str, ...]:
    """Validate season types and put them in canonical order (regular, playin, playoffs)."""
    chosen = set(season_types)
    unknown = chosen - set(SEASON_TYPES)
    if unknown:
        raise ValueError(f"unknown season type(s) {sorted(unknown)}; expected {SEASON_TYPES}")
    if not chosen:
        raise ValueError("at least one season type is required")
    return tuple(t for t in SEASON_TYPES if t in chosen)


def build_units(
    request: BackfillRequest,
    *,
    teams_scope: str = NbaApiTeamSource.name,
    season_type_labels: Mapping[str, str] = NbaApiTeamStatsSource.season_type_labels,
) -> list[Unit]:
    """Units in load order (FK order, 1-4): teams, then per season its schedule and game logs.

    Scope keys are the ones the loaders write (load_teams, load_season_schedule,
    load_season_game_log), which is what makes resume find them.
    """
    units = [Unit(JOB_TEAMS, teams_scope)]
    for season in request.seasons:
        units.append(Unit(JOB_SCHEDULE, f"season={season}", season))
        for season_type in request.season_types:
            label = season_type_labels[season_type]
            units.append(
                Unit(JOB_GAMELOG, f"season={season};season_type={label}", season, season_type)
            )
    return units


def last_statuses(engine: Engine, units: Sequence[Unit]) -> dict[tuple[str, str], tuple[str, int]]:
    """Latest run (status, run_id) per unit, for the plan's reasons. Read only."""
    stmt = (
        select(RUNS.c.job_type, RUNS.c.scope_key, RUNS.c.status, RUNS.c.run_id)
        .where(RUNS.c.job_type.in_({u.job_type for u in units}))
        .where(RUNS.c.scope_key.in_({u.scope_key for u in units}))
        .order_by(RUNS.c.run_id.desc())
    )
    latest: dict[tuple[str, str], tuple[str, int]] = {}
    with engine.connect() as conn:
        for row in conn.execute(stmt):
            latest.setdefault((row.job_type, row.scope_key), (row.status, row.run_id))
    return latest


def plan_backfill(
    engine: Engine,
    request: BackfillRequest,
    *,
    now: datetime | None = None,
    **unit_options: Any,
) -> list[PlannedUnit]:
    """Decide run or skip for every unit (resume rule in the module docstring). Read only."""
    now = now if now is not None else datetime.now(UTC)
    units = build_units(request, **unit_options)
    last = last_statuses(engine, units)
    planned: list[PlannedUnit] = []
    for unit in units:
        previous = last.get((unit.job_type, unit.scope_key))
        history = f"last {previous[0]} (run {previous[1]})" if previous else "never run"
        if request.force:
            planned.append(PlannedUnit(unit, RUN, f"--force; {history}"))
            continue
        if unit.season is not None and now < season_over_at(unit.season):
            planned.append(PlannedUnit(unit, RUN, f"season not over; {history}"))
            continue
        since = season_over_at(unit.season) if unit.season is not None else None
        if has_succeeded(engine, unit.job_type, unit.scope_key, started_after=since):
            planned.append(PlannedUnit(unit, SKIP, f"done; {history}"))
        else:
            planned.append(PlannedUnit(unit, RUN, history))
    return planned


@dataclass(frozen=True)
class Estimate:
    """Expected calls and duration of the units that will run (no retries)."""

    calls: int
    seconds: float
    per_unit: tuple[float, ...]


def estimate(plan: Sequence[PlannedUnit], min_interval: float) -> Estimate:
    """calls x (interval + assumed response time), per unit and in total."""
    per_unit = tuple(
        p.unit.calls * (min_interval + ASSUMED_RESPONSE_SECONDS[p.unit.job_type])
        if p.action == RUN
        else 0.0
        for p in plan
    )
    calls = sum(p.unit.calls for p in plan if p.action == RUN)
    return Estimate(calls, sum(per_unit), per_unit)


@dataclass(frozen=True)
class BackfillSources:
    """The sources a backfill loads from."""

    teams: TeamSource
    schedule: ScheduleSource
    team_stats: TeamStatsSource

    @classmethod
    def nba_api(cls, client: IngestionClient) -> BackfillSources:
        """nba_api sources sharing one client (one pacing clock for every call)."""
        return cls(
            NbaApiTeamSource(client), NbaApiScheduleSource(client), NbaApiTeamStatsSource(client)
        )


@dataclass(frozen=True)
class UnitResult:
    """How one unit ended, as recorded in its ingestion_runs row (when it has one)."""

    unit: Unit
    status: str  # a run status, or SKIPPED / NOT_RUN / NOT_STARTED
    run_id: int | None = None
    api_calls: int = 0
    retry_count: int = 0
    items_total: int | None = None
    items_succeeded: int = 0
    items_failed: int = 0
    rows_written: int = 0
    error: str | None = None
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        """Succeeded now or earlier (skipped)."""
        return self.status in (SUCCEEDED, SKIPPED)

    @property
    def left_rows(self) -> bool:
        """The unit stored at least part of its data (so dependent units can still run)."""
        return self.status in (SUCCEEDED, SKIPPED, PARTIAL)


@dataclass
class BackfillReport:
    """Everything the CLI prints at the end."""

    request: BackfillRequest
    plan: list[PlannedUnit]
    parent_run_id: int | None = None
    parent_status: str | None = None
    results: list[UnitResult] = field(default_factory=list)
    blocked: str | None = None
    games_in_range: tuple[int, int] | None = None  # (games, final games) for a date range

    @property
    def failures(self) -> list[UnitResult]:
        """Units that did not end succeeded or skipped."""
        return [r for r in self.results if not r.ok]

    @property
    def exit_code(self) -> int:
        """0 all done, 1 any unit not done, 2 stopped by a block (403/429)."""
        if self.blocked is not None:
            return EXIT_BLOCKED
        not_reached = len(self.results) < len(self.plan)
        return EXIT_FAILED if self.failures or not_reached else EXIT_OK


def child_result(
    engine: Engine, parent_run_id: int, unit: Unit, seconds: float, error: str | None
) -> UnitResult:
    """Read back the unit's run row under this parent (the loader opened and closed it)."""
    stmt = (
        select(RUNS)
        .where(RUNS.c.parent_run_id == parent_run_id)
        .where(RUNS.c.job_type == unit.job_type, RUNS.c.scope_key == unit.scope_key)
        .order_by(RUNS.c.run_id.desc())
        .limit(1)
    )
    with engine.connect() as conn:
        row = conn.execute(stmt).first()
    if row is None:
        return UnitResult(unit, NOT_STARTED, error=error, seconds=seconds)
    return UnitResult(
        unit,
        row.status,
        run_id=row.run_id,
        api_calls=row.api_calls,
        retry_count=row.retry_count,
        items_total=row.items_total,
        items_succeeded=row.items_succeeded,
        items_failed=row.items_failed,
        rows_written=row.rows_written,
        error=error or row.error_message,
        seconds=seconds,
    )


def count_rows(engine: Engine, table: Any, *where: Any) -> int:
    """Row count of a table, optionally filtered."""
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(table).where(*where)).scalar_one()


def games_in_range(engine: Engine, start: date, end: date) -> tuple[int, int]:
    """(games, final games) whose start falls on the inclusive ET date range."""
    lower, upper = et_day_bounds(start, end)
    in_range = (GAMES.c.start_time_utc >= lower, GAMES.c.start_time_utc < upper)
    return count_rows(engine, GAMES, *in_range), count_rows(
        engine, GAMES, *in_range, GAMES.c.status_code == 3
    )


def missing_prerequisite(
    engine: Engine, unit: Unit, done: Mapping[tuple[str, str | None], UnitResult]
) -> str | None:
    """Why the unit cannot run (its prerequisite failed now and left no rows), or None."""
    teams = done.get((JOB_TEAMS, None))
    if (
        unit.job_type != JOB_TEAMS
        and teams is not None
        and not teams.left_rows
        and count_rows(engine, TEAMS) == 0
    ):
        return "teams failed and the teams table is empty"
    schedule = done.get((JOB_SCHEDULE, unit.season))
    if (
        unit.job_type == JOB_GAMELOG
        and schedule is not None
        and not schedule.left_rows
        and count_rows(engine, GAMES, GAMES.c.season == unit.season) == 0
    ):
        return f"schedule {unit.season} failed and no games of that season are stored"
    return None


def run_unit(
    unit: Unit, sources: BackfillSources, engine: Engine, stale_after_hours: float | None
) -> None:
    """Load one unit through its loader (which opens and closes the child run)."""
    if unit.job_type == JOB_TEAMS:
        load_teams(sources.teams, engine, stale_after_hours=stale_after_hours)
    elif unit.job_type == JOB_SCHEDULE and unit.season is not None:
        load_season_schedule(
            sources.schedule, engine, unit.season, stale_after_hours=stale_after_hours
        )
    elif unit.job_type == JOB_GAMELOG and unit.season is not None and unit.season_type:
        load_season_game_log(
            sources.team_stats,
            engine,
            unit.season,
            unit.season_type,
            stale_after_hours=stale_after_hours,
        )
    else:
        raise ValueError(f"no loader for unit {unit}")


def run_backfill(
    engine: Engine,
    request: BackfillRequest,
    plan: Sequence[PlannedUnit],
    sources: BackfillSources,
    *,
    stale_after_hours: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> BackfillReport:
    """Execute the plan under one 'backfill' parent run; a unit's failure never stops it.

    Only a BlockedError stops early. Other exceptions of a unit are already recorded in its
    run row by track_run; here they become that unit's result. Raises RunAlreadyRunningError
    when the same backfill (same scope_key) is still running.
    """
    report = BackfillReport(request, list(plan))
    done: dict[tuple[str, str | None], UnitResult] = {}
    try:
        with track_run(
            engine,
            JOB_BACKFILL,
            request.scope_key(),
            source=NbaApiTeamSource.name,
            season=request.seasons[0] if len(request.seasons) == 1 else None,
            items_total=len(plan),
            stale_after_hours=stale_after_hours,
        ) as parent:
            report.parent_run_id = parent.run_id
            parent.detail["plan"] = [
                {"job_type": p.unit.job_type, "scope_key": p.unit.scope_key, "action": p.action}
                for p in plan
            ]
            if request.force:
                parent.detail["force"] = True
            for number, planned in enumerate(plan, 1):
                unit = planned.unit
                blocked: BlockedError | None = None
                why_not = (
                    None if planned.action == SKIP else missing_prerequisite(engine, unit, done)
                )
                if planned.action == SKIP:
                    result = UnitResult(unit, SKIPPED)
                elif why_not is not None:
                    result = UnitResult(unit, NOT_RUN, error=why_not)
                else:
                    logger.info(
                        "[%d/%d] %s %s ...", number, len(plan), unit.job_type, unit.scope_key
                    )
                    started = clock()
                    error: str | None = None
                    try:
                        run_unit(unit, sources, engine, stale_after_hours)
                    except BlockedError as exc:
                        blocked, error = exc, error_line(exc)
                    except Exception as exc:  # recorded per unit; the next unit still runs
                        error = error_line(exc)
                        logger.debug("%s %s failed", unit.job_type, unit.scope_key, exc_info=True)
                    result = child_result(engine, parent.run_id, unit, clock() - started, error)
                done[(unit.job_type, unit.season)] = result
                report.results.append(result)
                log = logger.info if result.ok else logger.warning
                log(
                    "[%d/%d] %s %s: %s%s",
                    number,
                    len(plan),
                    unit.job_type,
                    unit.scope_key,
                    result.status,
                    f" ({result.error})" if result.error else "",
                )
                if result.ok:
                    parent.item_succeeded()
                else:
                    parent.item_failed(unit.scope_key, f"{result.status}: {result.error or '-'}")
                if blocked is not None:
                    # 403/429: call nothing more (D-024); track_run closes the parent 'aborted'.
                    report.blocked = result.error or error_line(blocked)
                    raise blocked
            parent.detail["totals"] = totals(report.results)
    except BlockedError:
        if report.blocked is None:
            raise
    if report.parent_run_id is not None:
        with engine.connect() as conn:
            report.parent_status = conn.execute(
                select(RUNS.c.status).where(RUNS.c.run_id == report.parent_run_id)
            ).scalar_one()
    if request.date_from is not None and request.date_to is not None:
        report.games_in_range = games_in_range(engine, request.date_from, request.date_to)
    return report


def totals(results: Iterable[UnitResult]) -> dict[str, int]:
    """Calls, retries and rows over all units, for the parent's error_detail only."""
    # Not in the parent's own api_calls/rows_written: a SUM over runs would count them twice.
    results = list(results)
    return {
        "api_calls": sum(r.api_calls for r in results),
        "retries": sum(r.retry_count for r in results),
        "rows_written": sum(r.rows_written for r in results),
    }
