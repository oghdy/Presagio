"""Daily ingest (`presagio ingest-daily`): yesterday's results, today's schedule, odds (P-017).

Dates (completion criterion 1). The base date D is a Korean calendar date (Asia/Seoul), by
default today in Korea. Games that started on US Eastern date D-1 are "yesterday's games"
(results); games starting on ET date D are "today's games" (schedule, odds). ET dates are
what NBA scoreboards use, so they are passed to ScoreboardV3 explicitly (A-2, research 6) and
turned into UTC windows with zoneinfo (23 or 25 hours on DST days, schema 1-2.4).

Steps, all children of one parent run (job_type 'incremental', scope 'date_kst=<D>'):
  teams     team list, unless a teams run succeeded in the last TEAMS_REFRESH_HOURS (the
            backfill never reloads teams once done, D-037)
  results   ScoreboardV3(X) -> games (6-1 "scoreboardv3" columns) for X = D-1 ET plus the
            retry dates; each X is a date unit (schedule, 'date_et=<X>') kept open until the
            game log step is done
  gamelog   LeagueGameLog of the season, once per season type of the unfinished games on
            those dates -> games results + team_game_stats (A-6: daily stats are game logs)
  upcoming  ScoreboardV3(D) -> games (schedule, 'date_et=<D>;phase=upcoming')
  odds      The Odds API /odds once -> odds_events + odds_snapshots; 'skipped' without a key
  checks    where P-020's quality checks plug in (none yet)
`--odds-only` runs only the odds step (closing captures, no nba_api call).

Date unit success (schema ch. 4, Q9): X ends 'succeeded' only when every stored-type game of
X is final and has its two leaguegamelog team lines (the 006 cup final, which no game log
lists, needs only to be final). Otherwise 'partial' with the remaining game IDs, and the next
run fetches X again: a retry date is an ET date of the last RETRY_DAYS days (before D-1) whose
latest date-unit run is not 'succeeded', or that only had an upcoming run (a missed day). A
date unit that already succeeded is skipped (resume, like the backfill) unless --force.

Failures: a step that fails is recorded and the next step runs. A 403/429 stops at once and
the parent ends 'aborted' (D-024). Exit codes as in the backfill (D-037): 0, 1, 2, 64.
All writes are upserts; running the same day twice leaves games and team_game_stats as they
were. Odds are snapshots: a later capture with new prices adds rows at its own captured_at.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time as monotonic
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, and_, func, or_, select

from presagio.config import Settings, get_settings, parse_clock_times
from presagio.db.models import Game, IngestionRun, TeamAlias, TeamGameStat
from presagio.ingestion.backfill import (
    EXIT_BLOCKED,
    EXIT_FAILED,
    EXIT_OK,
    NOT_STARTED,
    RUN,
    SKIP,
    SKIPPED,
    count_rows,
    et_day_bounds,
    season_of_date,
)
from presagio.ingestion.client import IngestionClient, db_raw_store
from presagio.ingestion.errors import BlockedError, RunAlreadyRunningError
from presagio.ingestion.loaders.games import (
    JOB_GAMELOG,
    JOB_SCHEDULE,
    STATUS_FINAL,
    ScoreboardSource,
    load_scoreboard,
    upsert_scoreboard,
)
from presagio.ingestion.loaders.odds import JOB_ODDS, load_odds, seed_odds_team_aliases
from presagio.ingestion.loaders.team_stats import load_season_game_log
from presagio.ingestion.loaders.teams import JOB_TEAMS, TEAMS, load_teams
from presagio.ingestion.runs import (
    ABORTED,
    FAILED,
    PARTIAL,
    RUNNING,
    SUCCEEDED,
    RunTracker,
    abort_stale_runs,
    error_line,
    start_run,
    track_run,
)
from presagio.ingestion.sources.base import (
    RESULT_SEASON_TYPES,
    TeamSource,
    TeamStatsSource,
)
from presagio.ingestion.sources.nba_api import NbaApiTeamSource
from presagio.ingestion.sources.nba_api.scoreboard import NbaApiScoreboardSource
from presagio.ingestion.sources.nba_api.team_stats import NbaApiTeamStatsSource
from presagio.ingestion.sources.odds.base import OddsSource
from presagio.ingestion.sources.odds.the_odds_api import SOURCE as THE_ODDS_API
from presagio.ingestion.sources.odds.the_odds_api import TheOddsApiSource

logger = logging.getLogger(__name__)

JOB_INCREMENTAL = "incremental"
SEOUL = ZoneInfo("Asia/Seoul")
EASTERN = ZoneInfo("America/New_York")
# Earlier ET dates (before D-1) a run looks back at for unfinished date units.
RETRY_DAYS = 3
# A teams run that succeeded this recently (DB clock) makes the teams step skip. Under 24 h so
# a daily run a little earlier than yesterday's still refreshes; two runs a day load once.
TEAMS_REFRESH_HOURS = 20.0
# odds_snapshots.capture_label values (reference only, never used by logic; schema 7-2).
LABEL_PREDICTION = "prediction"
LABEL_CLOSING = "pre_tip"
# The 006 cup final is in no game log (6-2, A-6): final status alone completes it.
NO_GAME_LOG_TYPES = frozenset({"cup_final"})
GAME_LOG_SOURCE = "leaguegamelog"

# Steps, in the order they run.
STEP_TEAMS = "teams"
STEP_RESULTS = "results"
STEP_GAMELOG = "gamelog"
STEP_UPCOMING = "upcoming"
STEP_ODDS = "odds"
STEP_CHECKS = "checks"
NOT_REACHED = "not_reached"

# Assumed response time of one call (s), research ch. 4: small calls about 0.3-0.5 s, game logs
# up to 1.4 s. Used for the dry-run estimate only.
ASSUMED_RESPONSE_SECONDS = {
    STEP_TEAMS: 0.5,
    STEP_RESULTS: 0.5,
    STEP_GAMELOG: 1.5,
    STEP_UPCOMING: 0.5,
    STEP_ODDS: 0.5,
    STEP_CHECKS: 0.0,
}

RUNS = IngestionRun.__table__
GAMES = Game.__table__
STATS = TeamGameStat.__table__
ALIASES = TeamAlias.__table__

# A check gets the engine and the request and returns problems found (P-020 plugs in here).
DailyCheck = Callable[[Engine, "DailyRequest"], Sequence[str]]


# --- dates ------------------------------------------------------------------------------------


def korean_today(now: datetime) -> date:
    """The Korean calendar date at the given moment (the default base date D)."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(SEOUL).date()


def result_date(base: date) -> date:
    """ET date of yesterday's games for base date D: D-1."""
    return base - timedelta(days=1)


def date_scope(day: date) -> str:
    """scope_key of the date unit of an ET date (schema ch. 4)."""
    return f"date_et={day.isoformat()}"


def upcoming_scope(day: date) -> str:
    """scope_key of the schedule load of today's ET date (not a results unit)."""
    return f"{date_scope(day)};phase=upcoming"


def wall_time(day: date, clock: str, zone: ZoneInfo) -> datetime:
    """UTC moment of HH:MM wall-clock time on a day in a zone."""
    hours, minutes = (int(part) for part in clock.split(":"))
    return datetime.combine(day, time(hours, minutes), zone).astimezone(UTC)


@dataclass(frozen=True)
class Capture:
    """A planned run of the day: full run or closing odds capture, in UTC."""

    label: str
    at_utc: datetime


def capture_times(base: date, settings: Settings) -> list[Capture]:
    """The day's planned runs (D-027): the full run at D KST, closing captures on ET date D."""
    captures = [Capture("full run", wall_time(base, settings.ingest_daily_time_kst, SEOUL))]
    for clock in parse_clock_times(settings.odds_closing_times_et):
        captures.append(Capture("odds-only", wall_time(base, clock, EASTERN)))
    return sorted(captures, key=lambda c: c.at_utc)


# --- request and plan ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DailyRequest:
    """One `ingest-daily` invocation: base date D (Korean), mode and --force."""

    base_date: date
    odds_only: bool = False
    force: bool = False

    @property
    def yesterday_et(self) -> date:
        """ET date whose games get their results (D-1)."""
        return result_date(self.base_date)

    @property
    def today_et(self) -> date:
        """ET date whose schedule and odds are loaded (D)."""
        return self.base_date

    def scope_key(self) -> str:
        """Parent run scope_key: the same day and mode never run twice at once."""
        key = f"date_kst={self.base_date.isoformat()}"
        return f"{key};odds_only" if self.odds_only else key


@dataclass(frozen=True)
class PlannedStep:
    """One step, what this run will do about it and why; calls when it runs (no retries)."""

    step: str
    job_type: str | None
    scope_key: str
    action: str  # RUN or SKIP
    reason: str
    calls: int = 1
    target_date_et: date | None = None


@dataclass(frozen=True)
class LatestRun:
    """The latest run of one (job_type, scope_key)."""

    run_id: int
    status: str
    started_at: datetime


def latest_runs(engine: Engine, job_type: str, scope_keys: Iterable[str]) -> dict[str, LatestRun]:
    """Latest run (highest run_id) per scope_key of a job_type. Read only."""
    keys = list(scope_keys)
    if not keys:
        return {}
    stmt = (
        select(RUNS.c.scope_key, RUNS.c.run_id, RUNS.c.status, RUNS.c.started_at)
        .where(RUNS.c.job_type == job_type, RUNS.c.scope_key.in_(keys))
        .order_by(RUNS.c.run_id.desc())
    )
    latest: dict[str, LatestRun] = {}
    with engine.connect() as conn:
        for row in conn.execute(stmt):
            latest.setdefault(row.scope_key, LatestRun(row.run_id, row.status, row.started_at))
    return latest


def recent_teams_run(engine: Engine, scope_key: str, hours: float) -> LatestRun | None:
    """A teams run that succeeded within the last hours, by the database clock. Read only."""
    stmt = (
        select(RUNS.c.run_id, RUNS.c.status, RUNS.c.started_at)
        .where(RUNS.c.job_type == JOB_TEAMS, RUNS.c.scope_key == scope_key)
        .where(RUNS.c.status == SUCCEEDED)
        .where(RUNS.c.started_at > func.now() - timedelta(hours=hours))
        .order_by(RUNS.c.run_id.desc())
        .limit(1)
    )
    with engine.connect() as conn:
        row = conn.execute(stmt).first()
    return None if row is None else LatestRun(row.run_id, row.status, row.started_at)


def plan_result_dates(engine: Engine, request: DailyRequest) -> list[PlannedStep]:
    """Result date units: retry dates (oldest first), then D-1. Read only."""
    yesterday = request.yesterday_et
    earlier = [yesterday - timedelta(days=n) for n in range(RETRY_DAYS, 0, -1)]
    days = [*earlier, yesterday]
    results = latest_runs(engine, JOB_SCHEDULE, [date_scope(d) for d in days])
    upcoming = latest_runs(engine, JOB_SCHEDULE, [upcoming_scope(d) for d in earlier])
    planned: list[PlannedStep] = []
    for day in days:
        last = results.get(date_scope(day))
        history = f"last {last.status} (run {last.run_id})" if last else "never run"
        step = PlannedStep(STEP_RESULTS, JOB_SCHEDULE, date_scope(day), RUN, "", target_date_et=day)
        if day == yesterday:
            if last is not None and last.status == SUCCEEDED and not request.force:
                planned.append(_with(step, SKIP, f"done; {history}"))
            else:
                planned.append(_with(step, RUN, f"yesterday (ET); {history}"))
            continue
        # An earlier date runs again only if the daily job already knew it: its results were
        # not complete, or it was "today" once and the next day's run never came.
        if last is not None and last.status != SUCCEEDED:
            planned.append(_with(step, RUN, f"retry; {history}"))
        elif last is None and upcoming_scope(day) in upcoming:
            planned.append(_with(step, RUN, "retry; schedule loaded, results never fetched"))
    return planned


def _with(step: PlannedStep, action: str, reason: str) -> PlannedStep:
    """A copy of a planned step with its action and reason."""
    return PlannedStep(
        step.step,
        step.job_type,
        step.scope_key,
        action,
        reason,
        step.calls if action == RUN else 0,
        step.target_date_et,
    )


def plan_game_logs(
    engine: Engine,
    days: Sequence[date],
    *,
    only_unfinished: bool = True,
    listed: Iterable[str] = (),
) -> list[tuple[str, str]]:
    """(season, season type) game logs the dates need: their games not complete yet. Read only.

    With only_unfinished False (--force), every season type played on those dates. listed
    adds games a scoreboard put on those dates whatever their start time.
    """
    wanted: set[tuple[str, str]] = set()
    listed = list(listed)
    for day in days:
        for game in date_games(engine, day, listed):
            if game.game_type not in RESULT_SEASON_TYPES:
                continue
            if only_unfinished and game.complete:
                continue
            wanted.add((game.season, game.game_type))
    order = {season_type: n for n, season_type in enumerate(RESULT_SEASON_TYPES)}
    return sorted(wanted, key=lambda pair: (pair[0], order[pair[1]]))


@dataclass(frozen=True)
class DailyPlan:
    """Every step of one run, in order."""

    request: DailyRequest
    steps: list[PlannedStep]
    odds_enabled: bool

    @property
    def result_dates(self) -> list[date]:
        """ET dates whose results this run fetches."""
        return [
            s.target_date_et
            for s in self.steps
            if s.step == STEP_RESULTS and s.action == RUN and s.target_date_et is not None
        ]


def plan_daily(
    engine: Engine,
    request: DailyRequest,
    *,
    odds_enabled: bool,
    teams_scope: str = NbaApiTeamSource.name,
    checks: Sequence[DailyCheck] = (),
) -> DailyPlan:
    """Decide run or skip for every step (rules in the module docstring). Read only.

    The game log step is re-decided after the scoreboards (a game may become final there).
    """
    steps: list[PlannedStep] = []
    if not request.odds_only:
        recent = recent_teams_run(engine, teams_scope, TEAMS_REFRESH_HOURS)
        if recent is not None and not request.force:
            steps.append(
                PlannedStep(
                    STEP_TEAMS,
                    JOB_TEAMS,
                    teams_scope,
                    SKIP,
                    f"refreshed within {TEAMS_REFRESH_HOURS:g} h (run {recent.run_id})",
                    0,
                )
            )
        else:
            steps.append(
                PlannedStep(STEP_TEAMS, JOB_TEAMS, teams_scope, RUN, "daily team list refresh")
            )
        steps.extend(plan_result_dates(engine, request))
        days = [s.target_date_et for s in steps if s.step == STEP_RESULTS and s.action == RUN]
        logs = plan_game_logs(engine, [d for d in days if d], only_unfinished=not request.force)
        if logs:
            for season, season_type in logs:
                label = NbaApiTeamStatsSource.season_type_labels[season_type]
                steps.append(
                    PlannedStep(
                        STEP_GAMELOG,
                        JOB_GAMELOG,
                        f"season={season};season_type={label}",
                        RUN,
                        "unfinished games on result dates (re-decided after the scoreboards)",
                    )
                )
        else:
            steps.append(
                PlannedStep(
                    STEP_GAMELOG,
                    JOB_GAMELOG,
                    "-",
                    SKIP,
                    "no unfinished game on result dates in the DB now "
                    "(re-decided after the scoreboards)",
                    0,
                )
            )
        steps.append(
            PlannedStep(
                STEP_UPCOMING,
                JOB_SCHEDULE,
                upcoming_scope(request.today_et),
                RUN,
                "today (ET): schedule, times, new games",
                target_date_et=request.today_et,
            )
        )
    label = LABEL_CLOSING if request.odds_only else LABEL_PREDICTION
    if odds_enabled:
        steps.append(PlannedStep(STEP_ODDS, JOB_ODDS, THE_ODDS_API, RUN, f"capture '{label}'"))
    else:
        steps.append(
            PlannedStep(STEP_ODDS, JOB_ODDS, THE_ODDS_API, SKIP, "ODDS_API_KEY is not set", 0)
        )
    if not request.odds_only:
        reason = f"{len(checks)} check(s)" if checks else "no quality checks wired yet (P-020)"
        steps.append(PlannedStep(STEP_CHECKS, None, "-", RUN if checks else SKIP, reason, 0))
    return DailyPlan(request, steps, odds_enabled)


@dataclass(frozen=True)
class Estimate:
    """Expected calls and seconds of the steps that will run (no retries)."""

    calls: int
    seconds: float


def estimate(plan: DailyPlan, min_interval: float) -> Estimate:
    """calls x (interval + assumed response time)."""
    running = [s for s in plan.steps if s.action == RUN]
    calls = sum(s.calls for s in running)
    seconds = sum(s.calls * (min_interval + ASSUMED_RESPONSE_SECONDS[s.step]) for s in running)
    return Estimate(calls, seconds)


# --- completeness of a date ---------------------------------------------------------------------


@dataclass(frozen=True)
class DateGame:
    """A stored game of an ET date and whether its results are complete."""

    game_id: str
    season: str
    game_type: str
    status_code: int
    status_text: str
    game_log_lines: int

    @property
    def complete(self) -> bool:
        """Final, with both leaguegamelog team lines (or a type no game log lists)."""
        if self.status_code != STATUS_FINAL:
            return False
        return self.game_type in NO_GAME_LOG_TYPES or self.game_log_lines == 2

    @property
    def missing(self) -> str:
        """Why the game is not complete (empty when it is)."""
        if self.status_code != STATUS_FINAL:
            return f"not final (status {self.status_code}, {self.status_text!r})"
        if not self.complete:
            return f"final, {self.game_log_lines} of 2 game log team lines"
        return ""


def date_games(engine: Engine, day: date, also: Iterable[str] = ()) -> list[DateGame]:
    """Stored games starting on the ET date (plus the given IDs), with game log line counts."""
    lower, upper = et_day_bounds(day, day)
    in_window = and_(GAMES.c.start_time_utc >= lower, GAMES.c.start_time_utc < upper)
    extra = list(also)
    where = or_(in_window, GAMES.c.game_id.in_(extra)) if extra else in_window
    lines = (
        select(STATS.c.game_id, func.count().label("lines"))
        .where(STATS.c.source == GAME_LOG_SOURCE)
        .group_by(STATS.c.game_id)
        .subquery()
    )
    stmt = (
        select(
            GAMES.c.game_id,
            GAMES.c.season,
            GAMES.c.game_type,
            GAMES.c.status_code,
            GAMES.c.status_text,
            func.coalesce(lines.c.lines, 0).label("lines"),
        )
        .select_from(GAMES.outerjoin(lines, lines.c.game_id == GAMES.c.game_id))
        .where(where)
        .order_by(GAMES.c.game_id)
    )
    with engine.connect() as conn:
        return [
            DateGame(r.game_id, r.season, r.game_type, r.status_code, r.status_text, r.lines)
            for r in conn.execute(stmt)
        ]


# --- sources and results --------------------------------------------------------------------------


@dataclass(frozen=True)
class DailySources:
    """The sources a daily run loads from; odds is None when no key is configured."""

    teams: TeamSource
    scoreboard: ScoreboardSource
    team_stats: TeamStatsSource
    odds: OddsSource | None

    @classmethod
    def from_settings(cls, client: IngestionClient, settings: Settings) -> DailySources:
        """nba_api sources and The Odds API (if ODDS_API_KEY is set), sharing one client."""
        return cls(
            NbaApiTeamSource(client),
            NbaApiScoreboardSource(client),
            NbaApiTeamStatsSource(client),
            TheOddsApiSource.from_settings(client, settings),
        )


@dataclass(frozen=True)
class StepResult:
    """How one step ended, as its ingestion_runs row says (SKIPPED/NOT_STARTED have none)."""

    step: str
    job_type: str | None
    scope_key: str
    status: str
    run_id: int | None = None
    api_calls: int = 0
    retry_count: int = 0
    items_total: int | None = None
    items_succeeded: int = 0
    items_failed: int = 0
    rows_written: int = 0
    error: str | None = None
    seconds: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Succeeded, or skipped on purpose."""
        return self.status in (SUCCEEDED, SKIPPED)


@dataclass
class DailyReport:
    """Everything the CLI prints at the end."""

    plan: DailyPlan
    parent_run_id: int | None = None
    parent_status: str | None = None
    results: list[StepResult] = field(default_factory=list)
    blocked: str | None = None

    @property
    def failures(self) -> list[StepResult]:
        """Steps that did not end succeeded or skipped."""
        return [r for r in self.results if not r.ok]

    @property
    def exit_code(self) -> int:
        """0 all done, 1 any step not done (partial included), 2 stopped by a block."""
        if self.blocked is not None:
            return EXIT_BLOCKED
        return EXIT_FAILED if self.failures else EXIT_OK


def run_row(engine: Engine, run_id: int) -> Any:
    """One ingestion_runs row."""
    with engine.connect() as conn:
        return conn.execute(select(RUNS).where(RUNS.c.run_id == run_id)).one()


def child_row(engine: Engine, parent_run_id: int, job_type: str, scope_key: str) -> Any | None:
    """The latest run of (job_type, scope_key) under this parent, or None."""
    stmt = (
        select(RUNS)
        .where(RUNS.c.parent_run_id == parent_run_id)
        .where(RUNS.c.job_type == job_type, RUNS.c.scope_key == scope_key)
        .order_by(RUNS.c.run_id.desc())
        .limit(1)
    )
    with engine.connect() as conn:
        return conn.execute(stmt).first()


def result_of(step: PlannedStep, row: Any | None, seconds: float, error: str | None) -> StepResult:
    """StepResult from a step's run row (NOT_STARTED when the row was never opened)."""
    if row is None:
        return StepResult(
            step.step, step.job_type, step.scope_key, NOT_STARTED, error=error, seconds=seconds
        )
    return StepResult(
        step.step,
        step.job_type,
        step.scope_key,
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
        detail=dict(row.error_detail or {}),
    )


# --- running -------------------------------------------------------------------------------------


class Stop(Exception):
    """A step was blocked (403/429): nothing else may be called."""


@dataclass
class DateUnit:
    """An open date unit: its run, the games its scoreboard listed, scoreboard failures."""

    step: PlannedStep
    run: RunTracker
    started: float
    listed: tuple[str, ...] = ()
    failed: dict[str, str] = field(default_factory=dict)


class DailyRunner:
    """Executes a DailyPlan under one 'incremental' parent run."""

    def __init__(
        self,
        engine: Engine,
        plan: DailyPlan,
        sources: DailySources,
        *,
        stale_after_hours: float,
        checks: Sequence[DailyCheck] = (),
        clock: Callable[[], float] = monotonic.monotonic,
    ) -> None:
        """Keep what every step needs."""
        self.engine = engine
        self.plan = plan
        self.sources = sources
        self.stale_after_hours = stale_after_hours
        self.checks = checks
        self.clock = clock
        self.report = DailyReport(plan)
        self.parent: RunTracker | None = None

    def run(self) -> DailyReport:
        """Run every step; raises RunAlreadyRunningError if this day's run is still running."""
        request = self.plan.request
        try:
            with track_run(
                self.engine,
                JOB_INCREMENTAL,
                request.scope_key(),
                target_date_et=request.today_et,
                items_total=len(self.plan.steps),
                stale_after_hours=self.stale_after_hours,
            ) as parent:
                self.parent = parent
                self.report.parent_run_id = parent.run_id
                parent.detail.update(
                    base_date_kst=request.base_date.isoformat(),
                    yesterday_et=request.yesterday_et.isoformat(),
                    today_et=request.today_et.isoformat(),
                    plan=[
                        {"step": s.step, "scope_key": s.scope_key, "action": s.action}
                        for s in self.plan.steps
                    ],
                )
                if request.odds_only:
                    parent.detail["odds_only"] = True
                if request.force:
                    parent.detail["force"] = True
                try:
                    self._run_steps()
                finally:
                    parent.items_total = len(self.report.results)
                    parent.detail["steps"] = [
                        {
                            "step": r.step,
                            "scope_key": r.scope_key,
                            "status": r.status,
                            "run_id": r.run_id,
                        }
                        for r in self.report.results
                    ]
                    parent.detail["totals"] = totals(self.report.results)
        except BlockedError:
            if self.report.blocked is None:
                raise
        if self.report.parent_run_id is not None:
            self.report.parent_status = run_row(self.engine, self.report.parent_run_id).status
        return self.report

    def _run_steps(self) -> None:
        """Steps in order; a block raises out after closing what is open."""
        steps = self.plan.steps
        open_units: list[DateUnit] = []
        try:
            for step in (s for s in steps if s.step == STEP_TEAMS):
                self._loader_step(step, self._load_teams)
            for step in (s for s in steps if s.step == STEP_RESULTS):
                if step.action == SKIP:
                    self._record(StepResult(step.step, step.job_type, step.scope_key, SKIPPED))
                    continue
                unit = self._open_date_unit(step)
                if unit is not None:
                    open_units.append(unit)
            if any(s.step == STEP_GAMELOG for s in steps):
                self._game_logs(open_units)
            for unit in open_units:
                self._close_date_unit(unit)
            open_units = []
            for step in steps:
                if step.step == STEP_UPCOMING:
                    self._loader_step(step, self._load_upcoming)
                elif step.step == STEP_ODDS:
                    self._loader_step(step, self._load_odds)
                elif step.step == STEP_CHECKS:
                    self._checks_step(step)
        except Stop:
            for unit in open_units:
                if unit.run.status == RUNNING:
                    unit.run.finish(ABORTED, error=Stop("stopped: another step was blocked"))
                    self._record(
                        result_of(unit.step, run_row(self.engine, unit.run.run_id), 0, None)
                    )
            done = {(r.step, r.scope_key) for r in self.report.results}
            for step in steps:
                if (step.step, step.scope_key) not in done:
                    self.report.results.append(
                        StepResult(step.step, step.job_type, step.scope_key, NOT_REACHED)
                    )
            assert self.report.blocked is not None
            raise BlockedError(self.report.blocked, http_status=0) from None
        except Exception as exc:  # e.g. the database went away: leave nothing 'running'
            for unit in open_units:
                if unit.run.status == RUNNING:
                    unit.run.finish(FAILED, error=exc)
            raise

    def _record(self, result: StepResult) -> None:
        """Keep a step result and count it on the parent."""
        self.report.results.append(result)
        assert self.parent is not None
        log = logger.info if result.ok else logger.warning
        log(
            "%s %s: %s%s",
            result.step,
            result.scope_key,
            result.status,
            f" ({result.error})" if result.error else "",
        )
        if result.ok:
            self.parent.item_succeeded()
        else:
            self.parent.item_failed(
                f"{result.step} {result.scope_key}", f"{result.status}: {result.error or '-'}"
            )

    def _loader_step(self, step: PlannedStep, load: Callable[[PlannedStep], None]) -> None:
        """Run a step whose loader opens and closes its own child run."""
        if step.action == SKIP:
            self._record(
                StepResult(step.step, step.job_type, step.scope_key, SKIPPED, error=step.reason)
            )
            return
        assert self.parent is not None
        logger.info("%s %s ...", step.step, step.scope_key)
        started = self.clock()
        error: str | None = None
        blocked: BlockedError | None = None
        try:
            load(step)
        except BlockedError as exc:
            blocked, error = exc, error_line(exc)
        except Exception as exc:  # recorded per step; the next step still runs
            error = error_line(exc)
            logger.debug("%s %s failed", step.step, step.scope_key, exc_info=True)
        row = (
            child_row(self.engine, self.parent.run_id, step.job_type, step.scope_key)
            if step.job_type is not None
            else None
        )
        self._record(result_of(step, row, self.clock() - started, error))
        if blocked is not None:
            self.report.blocked = error
            raise Stop

    def _load_teams(self, step: PlannedStep) -> None:
        """Teams step."""
        load_teams(self.sources.teams, self.engine, stale_after_hours=self.stale_after_hours)

    def _load_upcoming(self, step: PlannedStep) -> None:
        """Upcoming step: today's ET scoreboard."""
        assert step.target_date_et is not None
        load_scoreboard(
            self.sources.scoreboard,
            self.engine,
            step.target_date_et,
            scope_key=step.scope_key,
            stale_after_hours=self.stale_after_hours,
        )

    def _load_odds(self, step: PlannedStep) -> None:
        """Odds step: seed the provider's team names if missing, then one capture."""
        odds = self.sources.odds
        assert odds is not None
        ensure_odds_aliases(self.engine, odds.name)
        label = LABEL_CLOSING if self.plan.request.odds_only else LABEL_PREDICTION
        load_odds(odds, self.engine, capture_label=label, stale_after_hours=self.stale_after_hours)

    def _open_date_unit(self, step: PlannedStep) -> DateUnit | None:
        """Fetch the date's scoreboard into its date unit run, left open for the game log."""
        assert self.parent is not None and step.target_date_et is not None
        day = step.target_date_et
        logger.info("%s %s ...", step.step, step.scope_key)
        started = self.clock()
        abort_stale_runs(self.engine, self.stale_after_hours, job_type=JOB_SCHEDULE)
        try:
            run = start_run(
                self.engine,
                JOB_SCHEDULE,
                step.scope_key,
                source=self.sources.scoreboard.name,
                parent=self.parent,
                season=season_of_date(day),
                target_date_et=day,
            )
        except RunAlreadyRunningError as exc:
            self._record(result_of(step, None, self.clock() - started, error_line(exc)))
            return None
        unit = DateUnit(step, run, started)
        try:
            batch = self.sources.scoreboard.fetch_scoreboard(day, run)
            with self.engine.begin() as conn:
                loaded = upsert_scoreboard(conn, batch)
        except BlockedError as exc:
            run.finish(ABORTED, error=exc)
            self._record(result_of(step, run_row(self.engine, run.run_id), 0, None))
            self.report.blocked = error_line(exc)
            raise Stop from None
        except Exception as exc:  # the unit failed; the game log may still fill the date
            logger.debug("%s %s failed", step.step, step.scope_key, exc_info=True)
            run.finish(FAILED, error=exc)
            self._record(
                result_of(step, run_row(self.engine, run.run_id), self.clock() - started, None)
            )
            return None
        unit.listed = tuple(g.game_id for g in batch.games)
        unit.failed = dict(loaded.failed)
        run.rows_written = loaded.counts.written
        run.detail.update(
            games=loaded.counts.as_dict(),
            skipped_game_types=loaded.skipped,
            raw_response_id=batch.provenance.raw_response_id,
        )
        if loaded.issues:
            run.detail.setdefault("quality_issues", []).extend(i.as_dict() for i in loaded.issues)
        return unit

    def _game_logs(self, units: Sequence[DateUnit]) -> None:
        """Game log step(s), decided now that the scoreboards are in."""
        days = [unit.step.target_date_et for unit in units if unit.step.target_date_et]
        planned = [s for s in self.plan.steps if s.step == STEP_GAMELOG]
        logs = plan_game_logs(
            self.engine,
            days,
            only_unfinished=not self.plan.request.force,
            listed=[game_id for unit in units for game_id in unit.listed],
        )
        if not logs:
            reason = "no unfinished game on the result dates" if days else "no result date ran"
            self._record(StepResult(STEP_GAMELOG, JOB_GAMELOG, "-", SKIPPED, error=reason))
            return
        for season, season_type in logs:
            label = self.sources.team_stats.season_type_labels[season_type]
            scope = f"season={season};season_type={label}"
            reason = next((s.reason for s in planned if s.scope_key == scope), "decided now")
            step = PlannedStep(STEP_GAMELOG, JOB_GAMELOG, scope, RUN, reason)
            self._loader_step(
                step,
                lambda _s, season=season, season_type=season_type: load_season_game_log(
                    self.sources.team_stats,
                    self.engine,
                    season,
                    season_type,
                    stale_after_hours=self.stale_after_hours,
                ),
            )

    def _close_date_unit(self, unit: DateUnit) -> None:
        """Close a date unit: succeeded only if every game is final with its stats (Q9)."""
        day = unit.step.target_date_et
        assert day is not None
        games = date_games(self.engine, day, unit.listed)
        run = unit.run
        # games holds stored game types only (A-4), so every game here counts.
        complete = [g for g in games if g.complete]
        remaining = {g.game_id: g.missing for g in games if not g.complete}
        run.items_total = len(games) + len([k for k in unit.failed if k not in remaining])
        for _ in complete:
            run.item_succeeded(0)
        for game_id, reason in unit.failed.items():
            run.item_failed(game_id, reason)
        if remaining:
            run.mark_partial(
                f"{len(remaining)} game(s) not complete yet; fetched again next run",
                remaining=remaining,
            )
        status = SUCCEEDED if not remaining and not unit.failed else PARTIAL
        run.finish(status)
        self._record(
            result_of(
                unit.step, run_row(self.engine, run.run_id), self.clock() - unit.started, None
            )
        )

    def _checks_step(self, step: PlannedStep) -> None:
        """Quality checks (P-020): each returns problems; any problem makes the step partial."""
        if step.action == SKIP or not self.checks:
            self._record(StepResult(step.step, None, "-", SKIPPED, error=step.reason))
            return
        problems: list[str] = []
        for check in self.checks:
            try:
                problems.extend(check(self.engine, self.plan.request))
            except Exception as exc:  # a broken check is a problem, not a crash
                problems.append(error_line(exc))
        status = PARTIAL if problems else SUCCEEDED
        self._record(
            StepResult(
                step.step,
                None,
                "-",
                status,
                items_total=len(self.checks),
                error="; ".join(problems)[:500] or None,
            )
        )


def ensure_odds_aliases(engine: Engine, source: str) -> None:
    """Seed The Odds API team names once (P-019 seed) if none are stored and teams exist."""
    if source != THE_ODDS_API:
        return
    if count_rows(engine, ALIASES, ALIASES.c.source == source) or not count_rows(engine, TEAMS):
        return
    with engine.begin() as conn:
        counts = seed_odds_team_aliases(conn)
    logger.info("seeded %d %s team name(s) into team_aliases", counts.written, source)


def totals(results: Iterable[StepResult]) -> dict[str, int]:
    """Calls, retries and rows over all steps, for the parent's error_detail only (D-037)."""
    results = list(results)
    return {
        "api_calls": sum(r.api_calls for r in results),
        "retries": sum(r.retry_count for r in results),
        "rows_written": sum(r.rows_written for r in results),
    }


def run_daily(
    engine: Engine,
    plan: DailyPlan,
    sources: DailySources,
    *,
    stale_after_hours: float | None = None,
    checks: Sequence[DailyCheck] = (),
    clock: Callable[[], float] = monotonic.monotonic,
) -> DailyReport:
    """Execute the plan; a step's failure never stops it, a block (403/429) does."""
    hours = stale_after_hours if stale_after_hours is not None else get_settings().stale_run_hours
    runner = DailyRunner(engine, plan, sources, stale_after_hours=hours, checks=checks, clock=clock)
    return runner.run()


# --- command line -------------------------------------------------------------------------------


def add_ingest_daily_parser(commands: Any) -> None:
    """Register `presagio ingest-daily` on the CLI's subparsers (called by presagio.cli)."""
    from presagio.cli import iso_date, positive_hours

    daily = commands.add_parser(
        "ingest-daily",
        help="daily ingest: yesterday's results and stats, today's schedule, odds",
        description=(
            "Base date D is a Korean date (default: today in Korea). Loads results and game "
            "log stats of games on US Eastern date D-1, the schedule of ET date D, and one "
            "odds capture (skipped without ODDS_API_KEY). Dates whose games are not all final "
            "end 'partial' and are fetched again by the next run."
        ),
    )
    daily.add_argument(
        "--date",
        dest="base_date",
        type=iso_date,
        metavar="YYYY-MM-DD",
        help="base date D in Korea (default: today in Asia/Seoul)",
    )
    daily.add_argument(
        "--odds-only",
        action="store_true",
        help="only capture odds (closing snapshots); no nba_api call",
    )
    daily.add_argument(
        "--dry-run", action="store_true", help="print the plan and expected calls; write nothing"
    )
    daily.add_argument(
        "--force", action="store_true", help="fetch yesterday and its game logs even if done"
    )
    daily.add_argument(
        "--stale-after-hours",
        type=positive_hours,
        metavar="H",
        help="close 'running' rows older than H hours as aborted first (default STALE_RUN_HOURS)",
    )
    daily.set_defaults(run=cmd_ingest_daily)


def utc_window(lower: datetime, upper: datetime) -> str:
    """'UTC [10-21 04:00, 10-22 04:00) 24 h' for an ET date window."""
    hours = (upper - lower).total_seconds() / 3600
    return f"UTC [{lower:%m-%d %H:%M}, {upper:%m-%d %H:%M}) {hours:g} h"


def print_dates(request: DailyRequest, settings: Settings) -> None:
    """The base date, the ET dates it means, their UTC windows and the day's planned runs."""
    print(f"Base date D = {request.base_date} (Korea, Asia/Seoul)")
    if not request.odds_only:
        lower, upper = et_day_bounds(request.yesterday_et, request.yesterday_et)
        print(f"  yesterday's games: ET {request.yesterday_et} = {utc_window(lower, upper)}")
    lower, upper = et_day_bounds(request.today_et, request.today_et)
    print(f"  today's games:     ET {request.today_et} = {utc_window(lower, upper)}")
    print("  planned runs (settings INGEST_DAILY_TIME_KST, ODDS_CLOSING_TIMES_ET; D-027):")
    for capture in capture_times(request.base_date, settings):
        at = capture.at_utc
        print(
            f"    {capture.label:<9}  {at.astimezone(SEOUL):%m-%d %H:%M} KST"
            f"  {at.astimezone(EASTERN):%m-%d %H:%M} ET  {at:%m-%d %H:%M} UTC"
        )


def print_plan(plan: DailyPlan, min_interval: float, dry_run: bool) -> None:
    """Steps with run/skip and why, and the expected calls and time."""
    from presagio.cli import format_table

    rows = [
        (
            n,
            s.step,
            s.job_type or "-",
            s.scope_key,
            s.action,
            s.reason,
            s.calls if s.action == RUN else 0,
        )
        for n, s in enumerate(plan.steps, 1)
    ]
    print(format_table(("#", "step", "job_type", "scope_key", "action", "reason", "calls"), rows))
    est = estimate(plan, min_interval)
    print(
        f"{'Would make' if dry_run else 'Plan'}: {est.calls} call(s), about {est.seconds:.0f} s "
        f"({min_interval:g} s interval + assumed response time; retries not counted)"
    )


def print_report(report: DailyReport) -> None:
    """Per-step outcome table, unfinished dates, odds summary and the exit status."""
    from presagio.cli import format_table

    rows = []
    for n, r in enumerate(report.results, 1):
        items = (
            f"{r.items_succeeded}/{r.items_failed}/{'' if r.items_total is None else r.items_total}"
            if r.run_id is not None
            else ""
        )
        rows.append(
            (
                n,
                r.step,
                r.scope_key,
                r.status,
                r.api_calls,
                items,
                r.rows_written,
                "" if r.run_id is None else r.run_id,
                round(r.seconds, 1),
            )
        )
    headers = (
        "#",
        "step",
        "scope_key",
        "status",
        "calls",
        "ok/failed/total",
        "rows",
        "run_id",
        "s",
    )
    print()
    print(format_table(headers, rows))
    calls = sum(r.api_calls for r in report.results)
    print(f"\nParent run {report.parent_run_id}: {report.parent_status}; {calls} call(s)")
    for r in report.results:
        if r.step == STEP_RESULTS and r.detail.get("remaining"):
            print(f"Not complete on {r.scope_key} (fetched again next run):")
            for game_id, why in r.detail["remaining"].items():
                print(f"  - {game_id}: {why}")
        if r.step == STEP_ODDS and r.run_id is not None:
            snaps = r.detail.get("snapshots", {})
            quota = r.detail.get("quota", {})
            print(
                f"Odds capture at {r.detail.get('captured_at')}: "
                f"{snaps.get('inserted', 0)} new price row(s), "
                f"{snaps.get('already_stored', 0)} already stored; "
                f"credits remaining {quota.get('x-requests-remaining', '?')}"
            )
    if report.failures:
        print(f"\nNot done ({len(report.failures)}):")
        for r in report.failures:
            print(f"  - {r.step} {r.scope_key}: {r.status}: {r.error or '-'}")
    if report.blocked is not None:
        print("\nStopped: the source blocked us (403/429). Wait before running again (D-024).")
    print(f"Exit code {report.exit_code}")


def cmd_ingest_daily(
    args: argparse.Namespace,
    *,
    engine: Engine | None = None,
    sources: Callable[[Engine], DailySources] | None = None,
    now: datetime | None = None,
    checks: Sequence[DailyCheck] = (),
) -> int:
    """presagio ingest-daily: plan, then (unless --dry-run) run and report."""
    settings = get_settings()
    try:
        if engine is None:
            from presagio.db.session import get_engine

            engine = get_engine()
        now = now if now is not None else datetime.now(UTC)
        base = args.base_date if args.base_date is not None else korean_today(now)
        request = DailyRequest(base, odds_only=args.odds_only, force=args.force)
        stale_after_hours = (
            args.stale_after_hours
            if args.stale_after_hours is not None
            else settings.stale_run_hours
        )
        built = (
            sources(engine)
            if sources is not None
            else DailySources.from_settings(IngestionClient(db_raw_store(engine)), settings)
        )
        mode = " (odds only)" * args.odds_only + " (dry run: nothing is written)" * args.dry_run
        print(f"Daily ingest{mode}")
        print_dates(request, settings)
        plan = plan_daily(engine, request, odds_enabled=built.odds is not None, checks=checks)
        print_plan(plan, settings.http_min_interval, args.dry_run)
        sys.stdout.flush()  # show the plan before the progress lines on stderr
        if args.dry_run:
            return EXIT_OK
        try:
            report = run_daily(
                engine, plan, built, stale_after_hours=stale_after_hours, checks=checks
            )
        except RunAlreadyRunningError as exc:
            print(
                f"\n{exc}. Another ingest-daily for the same day is running, or one died less "
                f"than {stale_after_hours:g} h ago (see --stale-after-hours).",
                file=sys.stderr,
            )
            return EXIT_FAILED
    except Exception as exc:  # e.g. database unreachable: one line, no traceback
        logger.debug("ingest-daily failed", exc_info=True)
        print(f"presagio: {error_line(exc)}", file=sys.stderr)
        return EXIT_FAILED
    print_report(report)
    return report.exit_code


__all__ = [
    "JOB_INCREMENTAL",
    "DailyPlan",
    "DailyReport",
    "DailyRequest",
    "DailySources",
    "add_ingest_daily_parser",
    "capture_times",
    "cmd_ingest_daily",
    "date_games",
    "korean_today",
    "plan_daily",
    "result_date",
    "run_daily",
]
