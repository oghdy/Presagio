"""Read-only data quality checks over the clean tables (risks B5, schema v1 ch. 6-8).

Every check is one or two SQL queries that only read. run_checks() runs them in one
REPEATABLE READ, READ ONLY transaction (a check that tried to write would fail), each inside a
savepoint, so one broken check is reported as 'error' and the others still run.

Statuses: pass; fail (data is wrong, exit code 1); warn (worth a look, exit code unchanged);
error (the check itself could not run, exit code 1); skip (not meaningful for this scope).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, Engine, bindparam, text

from presagio.ingestion.runs import error_line

EASTERN = ZoneInfo("America/New_York")

PASS = "pass"
FAIL = "fail"
WARN = "warn"
ERROR = "error"
SKIP = "skip"
STATUSES = (PASS, FAIL, WARN, ERROR, SKIP)

# 30 teams x 82 games / 2. Every season from 2023-24 on has exactly this many regular games
# (NBA Cup group and knockout games count, the Cup final is game type 006, not regular).
EXPECTED_REGULAR_GAMES = 1230
EXPECTED_TEAMS = 30
# Game types whose final games must have team stats from the game log (D-028 A-4, A-6).
# The Cup final (006) only gets a box score on demand, so it is not required.
STATS_GAME_TYPES = ("regular", "playin", "playoffs")
# team_aliases sources every team needs: nba_api names (D-034) and odds names (D-036).
ALIAS_SOURCES = ("nba_api", "the_odds_api")
# Odds mapping issue kinds the odds loader records per event (D-036), most specific first.
ODDS_MAPPING_ISSUES = (
    "unknown_team_alias",
    "ambiguous_game",
    "home_away_swapped",
    "no_matching_game",
)
# Raw nba_api responses that carry final scores, with the JSON path to their game objects.
SCORE_SOURCES = {
    "scheduleleaguev2": "$.leagueSchedule.gameDates[*].games[*]",
    "scoreboardv3": "$.scoreboard.games[*]",
}
MAX_EXAMPLES = 5


@dataclass(frozen=True)
class CheckSettings:
    """Tunable thresholds; the defaults fit full 82-game seasons."""

    expected_regular_games: int = EXPECTED_REGULAR_GAMES
    # A finished season must be within this many games of the expected count (raise it for a
    # shortened season).
    regular_games_tolerance: int = 0
    # A season in progress counts its scheduled games too; the schedule lists about 24 fewer
    # games until the NBA Cup knockout games are set, so it may be this far below and only warns.
    in_progress_tolerance: int = 30
    # Window of the ingestion run check, measured on the database clock.
    recent_run_days: int = 7
    # A game that started this long ago (database clock) should be final by now.
    past_game_grace_hours: int = 24


@dataclass(frozen=True)
class Scope:
    """Which games to check: one season, one US Eastern date, both, or everything."""

    season: str | None = None
    date_et: date | None = None

    def describe(self) -> str:
        """Human label, e.g. 'season 2023-24, ET 2024-01-05' or 'all seasons'."""
        parts = []
        if self.season is not None:
            parts.append(f"season {self.season}")
        if self.date_et is not None:
            parts.append(f"ET {self.date_et.isoformat()}")
        return ", ".join(parts) or "all seasons"

    def as_dict(self) -> dict[str, str | None]:
        """JSON-ready form."""
        return {
            "season": self.season,
            "date_et": None if self.date_et is None else self.date_et.isoformat(),
        }

    def games_where(self, alias: str = "g") -> tuple[str, dict[str, Any]]:
        """SQL conditions (each starting with AND) and parameters selecting the scoped games."""
        sql, params = "", {}
        if self.season is not None:
            sql += f" AND {alias}.season = :scope_season"
            params["scope_season"] = self.season
        if self.date_et is not None:
            lower, upper = et_day_bounds(self.date_et)
            sql += (
                f" AND {alias}.start_time_utc >= :scope_from AND {alias}.start_time_utc < :scope_to"
            )
            params.update(scope_from=lower, scope_to=upper)
        return sql, params

    def time_where(self, column: str) -> tuple[str, dict[str, Any]]:
        """Like games_where for rows that only have a time (odds): season = Jul 1 to Jul 1 ET."""
        bounds = []
        if self.season is not None:
            start = int(self.season[:4])
            bounds.append((et_midnight(date(start, 7, 1)), et_midnight(date(start + 1, 7, 1))))
        if self.date_et is not None:
            bounds.append(et_day_bounds(self.date_et))
        if not bounds:
            return "", {}
        lower = max(b[0] for b in bounds)
        upper = min(b[1] for b in bounds)
        return (
            f" AND {column} >= :scope_from AND {column} < :scope_to",
            {"scope_from": lower, "scope_to": upper},
        )


def et_midnight(day: date) -> datetime:
    """00:00 US Eastern on that day, in UTC."""
    return datetime.combine(day, time.min, EASTERN).astimezone(UTC)


def et_day_bounds(day: date) -> tuple[datetime, datetime]:
    """[start, end) in UTC of one US Eastern date (schema v1 rule 1-2.4)."""
    return et_midnight(day), et_midnight(day + timedelta(days=1))


@dataclass
class Outcome:
    """What a check function found: rows looked at, failing IDs, warning IDs, a note."""

    checked: int
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    note: str | None = None
    skipped: bool = False


@dataclass(frozen=True)
class CheckResult:
    """One row of the report."""

    name: str
    target: str
    status: str
    checked: int
    failed: int
    examples: tuple[str, ...]
    description: str
    note: str | None = None

    @property
    def passed(self) -> int:
        """Rows that passed."""
        return max(self.checked - self.failed, 0)

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready form."""
        return {
            "name": self.name,
            "target": self.target,
            "status": self.status,
            "checked": self.checked,
            "passed": self.passed,
            "failed": self.failed,
            "examples": list(self.examples),
            "description": self.description,
            "note": self.note,
        }


@dataclass(frozen=True)
class QualityReport:
    """Results of one run_checks call; exit_code is what the CLI returns."""

    scope: Scope
    results: tuple[CheckResult, ...]

    def count(self, status: str) -> int:
        """How many checks ended with this status."""
        return sum(1 for r in self.results if r.status == status)

    @property
    def ok(self) -> bool:
        """True when no check failed or errored (warnings and skips are fine)."""
        return not any(r.status in (FAIL, ERROR) for r in self.results)

    @property
    def exit_code(self) -> int:
        """0 when ok, else 1."""
        return 0 if self.ok else 1

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready form (for the CLI --json output and run error_detail)."""
        return {
            "scope": self.scope.as_dict(),
            "ok": self.ok,
            "exit_code": self.exit_code,
            "summary": {status: self.count(status) for status in STATUSES},
            "checks": [r.as_dict() for r in self.results],
        }


CheckFunction = Callable[[Connection, Scope, CheckSettings], Outcome]


@dataclass(frozen=True)
class Check:
    """A named check: what it looks at, what it means, and whether a finding fails or warns."""

    name: str
    target: str
    description: str
    function: CheckFunction
    finding: str = FAIL  # status of a check with failures: FAIL or WARN
    scoped: bool = True  # False: --season/--date do not apply (target says what it covers)

    def target_for(self, scope: Scope, cfg: CheckSettings) -> str:
        """Target column text, e.g. 'final games; season 2023-24'."""
        target = self.target.format(days=cfg.recent_run_days)
        return f"{target}; {scope.describe()}" if self.scoped else target


def _rows(conn: Connection, sql: str, params: dict[str, Any]) -> Sequence[Any]:
    """Execute a text query and return all rows; tuple parameters expand for IN."""
    stmt = text(sql).bindparams(
        *(bindparam(name, expanding=True) for name, v in params.items() if isinstance(v, tuple))
    )
    return conn.execute(stmt, params).all()


def check_team_rows_per_game(conn: Connection, scope: Scope, cfg: CheckSettings) -> Outcome:
    """Each final game has exactly two representative stat rows: home team and away team."""
    where, params = scope.games_where()
    rows = _rows(
        conn,
        f"""
        SELECT g.game_id,
               count(p.team_id) = 2
               AND count(*) FILTER (WHERE p.team_id = g.home_team_id AND p.is_home) = 1
               AND count(*) FILTER (WHERE p.team_id = g.away_team_id AND NOT p.is_home) = 1
                 AS ok
        FROM games g
        LEFT JOIN team_game_stats_primary p ON p.game_id = g.game_id
        WHERE g.status_code = 3 AND g.game_type IN :types {where}
        GROUP BY g.game_id, g.home_team_id, g.away_team_id
        ORDER BY g.game_id
        """,
        {**params, "types": STATS_GAME_TYPES},
    )
    return Outcome(len(rows), [r.game_id for r in rows if not r.ok])


def check_points_match_score(conn: Connection, scope: Scope, cfg: CheckSettings) -> Outcome:
    """Home pts + away pts = home_score + away_score, and home pts = home_score."""
    where, params = scope.games_where()
    rows = _rows(
        conn,
        f"""
        SELECT g.game_id,
               h.pts + a.pts = g.home_score + g.away_score AND h.pts = g.home_score AS ok
        FROM games g
        JOIN team_game_stats_primary h ON h.game_id = g.game_id AND h.team_id = g.home_team_id
        JOIN team_game_stats_primary a ON a.game_id = g.game_id AND a.team_id = g.away_team_id
        WHERE g.status_code = 3 {where}
        ORDER BY g.game_id
        """,
        params,
    )
    return Outcome(len(rows), [r.game_id for r in rows if not r.ok])


def check_final_scores_present(conn: Connection, scope: Scope, cfg: CheckSettings) -> Outcome:
    """No final game (status_code 3) has a NULL score; a DB CHECK guards it, this catches drift."""
    where, params = scope.games_where()
    rows = _rows(
        conn,
        f"""
        SELECT g.game_id, g.home_score IS NOT NULL AND g.away_score IS NOT NULL AS ok
        FROM games g
        WHERE g.status_code = 3 {where}
        ORDER BY g.game_id
        """,
        params,
    )
    return Outcome(len(rows), [r.game_id for r in rows if not r.ok])


def check_regular_season_games(conn: Connection, scope: Scope, cfg: CheckSettings) -> Outcome:
    """Regular games per season near 1,230; a season in progress counts scheduled games too."""
    if scope.date_et is not None:
        return Outcome(0, note="season totals are not meaningful for one date", skipped=True)
    where, params = scope.games_where()
    rows = _rows(
        conn,
        f"""
        SELECT g.season, count(*) AS total, count(*) FILTER (WHERE g.status_code = 3) AS final
        FROM games g
        WHERE g.game_type = 'regular' {where}
        GROUP BY g.season
        ORDER BY g.season
        """,
        params,
    )
    expected = cfg.expected_regular_games
    outcome = Outcome(len(rows))
    notes = []
    if scope.season is not None and not rows:
        outcome.checked = 1
        outcome.failures.append(f"{scope.season} (0)")
        notes.append(f"{scope.season}: no regular games")
    for row in rows:
        if row.final == row.total:
            notes.append(f"{row.season}: {row.final} final")
            if abs(row.final - expected) > cfg.regular_games_tolerance:
                outcome.failures.append(f"{row.season} ({row.final})")
        else:
            notes.append(
                f"{row.season}: in progress, {row.total} incl. scheduled ({row.final} final)"
            )
            low = expected - cfg.in_progress_tolerance
            if not low <= row.total <= expected + cfg.regular_games_tolerance:
                outcome.warnings.append(f"{row.season} ({row.total})")
    outcome.note = (
        f"expected {expected} +/- {cfg.regular_games_tolerance}; " + "; ".join(notes)
        if notes
        else None
    )
    return outcome


def check_one_game_per_team_day(conn: Connection, scope: Scope, cfg: CheckSettings) -> Outcome:
    """No team plays twice on one US Eastern date (the odds mapping relies on it, D-036)."""
    where, params = scope.games_where()
    rows = _rows(
        conn,
        f"""
        WITH team_games AS (
            SELECT g.game_id, t.team_id,
                   (g.start_time_utc AT TIME ZONE 'America/New_York')::date AS et_date
            FROM games g
            CROSS JOIN LATERAL (VALUES (g.home_team_id), (g.away_team_id)) AS t (team_id)
            WHERE g.home_team_id IS NOT NULL {where}
        )
        SELECT coalesce(tm.tricode, tg.team_id::text) AS team, tg.et_date,
               array_agg(tg.game_id ORDER BY tg.game_id) AS game_ids
        FROM team_games tg
        LEFT JOIN teams tm ON tm.team_id = tg.team_id
        GROUP BY tg.team_id, tm.tricode, tg.et_date
        ORDER BY tg.et_date, team
        """,
        params,
    )
    return Outcome(
        len(rows),
        [f"{r.team}@{r.et_date}:{'/'.join(r.game_ids)}" for r in rows if len(r.game_ids) > 1],
    )


def check_gamelog_vs_schedule_score(conn: Connection, scope: Scope, cfg: CheckSettings) -> Outcome:
    """Scores set from the game log equal the final score in the latest schedule/scoreboard raw.

    The daily flow checks game log points against games, which the game log itself just wrote
    (P-015 review), so this compares with a second endpoint kept in raw_api_responses.
    """
    where, params = scope.games_where()
    raw_games = " UNION ALL ".join(
        f"""
        SELECT r.raw_response_id, r.last_fetched_at, game
        FROM latest r, jsonb_path_query(r.response_json, '{path}') AS game
        WHERE r.endpoint = '{endpoint}'
        """
        for endpoint, path in SCORE_SOURCES.items()
    )
    rows = _rows(
        conn,
        f"""
        WITH latest AS (
            SELECT DISTINCT ON (r.request_hash) r.raw_response_id, r.endpoint, r.response_json,
                   r.last_fetched_at
            FROM raw_api_responses r
            WHERE r.source = 'nba_api' AND r.endpoint IN :endpoints
              AND r.http_status BETWEEN 200 AND 299 AND r.response_json IS NOT NULL
            ORDER BY r.request_hash, r.last_fetched_at DESC, r.raw_response_id DESC
        ),
        raw_games AS ({raw_games}),
        raw_final AS (
            SELECT DISTINCT ON (game ->> 'gameId')
                   game ->> 'gameId' AS game_id,
                   (game -> 'homeTeam' ->> 'teamId')::bigint AS home_id,
                   (game -> 'awayTeam' ->> 'teamId')::bigint AS away_id,
                   (game -> 'homeTeam' ->> 'score')::integer AS home_score,
                   (game -> 'awayTeam' ->> 'score')::integer AS away_score
            FROM raw_games
            WHERE game ->> 'gameStatus' = '3'
              AND jsonb_typeof(game -> 'homeTeam' -> 'score') = 'number'
              AND jsonb_typeof(game -> 'awayTeam' -> 'score') = 'number'
            ORDER BY game ->> 'gameId', last_fetched_at DESC, raw_response_id DESC
        )
        SELECT g.game_id,
               rf.game_id IS NOT NULL AS comparable,
               CASE g.home_team_id WHEN rf.home_id THEN rf.home_score
                                   WHEN rf.away_id THEN rf.away_score END = g.home_score
               AND CASE g.away_team_id WHEN rf.away_id THEN rf.away_score
                                       WHEN rf.home_id THEN rf.home_score END = g.away_score
                 AS ok
        FROM games g
        LEFT JOIN raw_final rf ON rf.game_id = g.game_id
        WHERE g.status_code = 3 AND g.source = 'leaguegamelog' {where}
        ORDER BY g.game_id
        """,
        {**params, "endpoints": tuple(SCORE_SOURCES)},
    )
    comparable = [r for r in rows if r.comparable]
    outcome = Outcome(len(comparable), [r.game_id for r in comparable if not r.ok])
    if len(comparable) < len(rows):
        outcome.note = (
            f"{len(rows) - len(comparable)} game-log game(s) have no final score in a schedule "
            "or scoreboard response yet (not counted)"
        )
    return outcome


def check_odds_unmatched_events(conn: Connection, scope: Scope, cfg: CheckSettings) -> Outcome:
    """Share of odds events without a game, with the mapping reason the odds loader recorded."""
    where, params = scope.time_where("e.commence_time")
    rows = _rows(
        conn,
        f"""
        WITH issues AS (
            SELECT DISTINCT ON (issue ->> 'key') issue ->> 'key' AS key, issue ->> 'kind' AS kind
            FROM ingestion_runs r
            CROSS JOIN LATERAL jsonb_array_elements(
                CASE jsonb_typeof(r.error_detail -> 'quality_issues')
                     WHEN 'array' THEN r.error_detail -> 'quality_issues' ELSE '[]' END
            ) AS issue
            WHERE r.job_type IN ('odds', 'odds_events') AND issue ->> 'kind' IN :kinds
            ORDER BY issue ->> 'key', r.started_at DESC, r.run_id DESC
        )
        SELECT e.source_event_id, e.game_id IS NOT NULL AS matched,
               coalesce(i.kind, 'no_recorded_reason') AS reason
        FROM odds_events e
        LEFT JOIN issues i ON i.key = e.source_event_id
        WHERE true {where}
        ORDER BY e.commence_time, e.source_event_id
        """,
        {**params, "kinds": ODDS_MAPPING_ISSUES},
    )
    unmatched = [r for r in rows if not r.matched]
    outcome = Outcome(len(rows), [r.source_event_id for r in unmatched])
    if rows:
        reasons = Counter(r.reason for r in unmatched)
        share = 100 * len(unmatched) / len(rows)
        outcome.note = f"{len(unmatched)}/{len(rows)} unmatched ({share:.1f}%)" + (
            "; " + ", ".join(f"{k} {n}" for k, n in sorted(reasons.items())) if reasons else ""
        )
    return outcome


def check_recent_failed_runs(conn: Connection, scope: Scope, cfg: CheckSettings) -> Outcome:
    """Runs of the last N days that ended partial/failed/aborted and were not redone later."""
    rows = _rows(
        conn,
        """
        SELECT r.run_id, r.job_type, r.scope_key, r.status,
               EXISTS (
                   SELECT 1 FROM ingestion_runs later
                   WHERE later.job_type = r.job_type AND later.scope_key = r.scope_key
                     AND later.status = 'succeeded' AND later.started_at > r.started_at
               ) AS redone
        FROM ingestion_runs r
        WHERE r.started_at >= now() - make_interval(days => :days) AND r.status <> 'running'
        ORDER BY r.run_id
        """,
        {"days": cfg.recent_run_days},
    )
    bad = [r for r in rows if r.status in ("partial", "failed", "aborted")]
    open_ = [r for r in bad if not r.redone]
    outcome = Outcome(len(rows), [f"{r.run_id}:{r.job_type}:{r.status}" for r in open_])
    if bad:
        outcome.note = (
            f"{len(open_)} not redone, {len(bad) - len(open_)} redone by a later succeeded run; "
            + ", ".join(f"{r.run_id} {r.scope_key}" for r in open_[:MAX_EXAMPLES])
        ).rstrip("; ")
    return outcome


def check_teams_and_aliases(conn: Connection, scope: Scope, cfg: CheckSettings) -> Outcome:
    """All 30 teams are in teams, each with an alias for nba_api and for the_odds_api."""
    rows = _rows(
        conn,
        """
        SELECT t.tricode, array_remove(array_agg(DISTINCT a.source), NULL) AS sources
        FROM teams t
        LEFT JOIN team_aliases a ON a.team_id = t.team_id AND a.source IN :sources
        GROUP BY t.tricode
        ORDER BY t.tricode
        """,
        {"sources": ALIAS_SOURCES},
    )
    outcome = Outcome(len(rows))
    if len(rows) != EXPECTED_TEAMS:
        outcome.failures.append(f"teams={len(rows)}")
    for row in rows:
        missing = [source for source in ALIAS_SOURCES if source not in row.sources]
        outcome.failures.extend(f"{row.tricode}:no_{source}_alias" for source in missing)
    return outcome


def check_odds_after_start(conn: Connection, scope: Scope, cfg: CheckSettings) -> Outcome:
    """No snapshot captured at or after its start, and no closing line after the event start."""
    where, params = scope.time_where("e.commence_time")
    snapshots = _rows(
        conn,
        f"""
        SELECT s.odds_snapshot_id, s.captured_at < s.commence_time AS ok
        FROM odds_snapshots s
        JOIN odds_events e ON e.odds_event_id = s.odds_event_id
        WHERE true {where}
        ORDER BY s.odds_snapshot_id
        """,
        params,
    )
    closing = _rows(
        conn,
        f"""
        SELECT c.odds_snapshot_id
        FROM odds_closing_lines c
        JOIN odds_events e ON e.odds_event_id = c.odds_event_id
        WHERE c.captured_at >= c.event_commence_time {where}
        ORDER BY c.odds_snapshot_id
        """,
        params,
    )
    return Outcome(
        len(snapshots),
        [str(r.odds_snapshot_id) for r in snapshots if not r.ok]
        + [f"closing:{r.odds_snapshot_id}" for r in closing],
    )


def check_primary_stats_source(conn: Connection, scope: Scope, cfg: CheckSettings) -> Outcome:
    """Outside the Cup final (006) the representative stats come from the game log (D-028 A-6)."""
    where, params = scope.games_where()
    rows = _rows(
        conn,
        f"""
        SELECT p.game_id, bool_and(p.source = 'leaguegamelog') AS ok
        FROM team_game_stats_primary p
        JOIN games g ON g.game_id = p.game_id
        WHERE g.game_type <> 'cup_final' {where}
        GROUP BY p.game_id
        ORDER BY p.game_id
        """,
        params,
    )
    return Outcome(len(rows), [r.game_id for r in rows if not r.ok])


def check_past_games_not_final(conn: Connection, scope: Scope, cfg: CheckSettings) -> Outcome:
    """Games that started over a day ago (database clock) but are still not final."""
    where, params = scope.games_where()
    rows = _rows(
        conn,
        f"""
        SELECT g.game_id, g.status_code = 3 AS ok
        FROM games g
        WHERE g.start_time_utc < now() - make_interval(hours => :grace) {where}
        ORDER BY g.start_time_utc, g.game_id
        """,
        {**params, "grace": cfg.past_game_grace_hours},
    )
    return Outcome(len(rows), [r.game_id for r in rows if not r.ok])


def check_odds_events_per_game(conn: Connection, scope: Scope, cfg: CheckSettings) -> Outcome:
    """At most one odds event per game (no UNIQUE on odds_events.game_id, schema 7-1)."""
    where, params = scope.games_where()
    rows = _rows(
        conn,
        f"""
        SELECT g.game_id, count(*) AS events
        FROM odds_events e
        JOIN games g ON g.game_id = e.game_id
        WHERE true {where}
        GROUP BY g.game_id
        ORDER BY g.game_id
        """,
        params,
    )
    return Outcome(len(rows), [r.game_id for r in rows if r.events > 1])


CHECKS: tuple[Check, ...] = (
    Check(
        "team_rows_per_game",
        "final games (regular, playin, playoffs)",
        "each final game has 2 team stat rows, 1 home",
        check_team_rows_per_game,
    ),
    Check(
        "points_match_score",
        "final games with both stat rows",
        "home pts + away pts = final score, home pts = home score",
        check_points_match_score,
    ),
    Check(
        "final_scores_present",
        "final games",
        "no NULL score on a final game",
        check_final_scores_present,
    ),
    Check(
        "regular_season_games",
        "seasons",
        "regular games per season = 1,230 (in progress: scheduled included, warn only)",
        check_regular_season_games,
    ),
    Check(
        "one_game_per_team_day",
        "team x ET date",
        "no team plays twice on one US Eastern date",
        check_one_game_per_team_day,
    ),
    Check(
        "gamelog_vs_schedule_score",
        "final games scored by leaguegamelog",
        "game-log score = final score in latest schedule/scoreboard raw",
        check_gamelog_vs_schedule_score,
    ),
    Check(
        "primary_stats_source",
        "games with stats (not cup_final)",
        "representative stats come from leaguegamelog",
        check_primary_stats_source,
    ),
    Check(
        "teams_and_aliases",
        "teams (all)",
        "30 teams, each with nba_api and the_odds_api aliases",
        check_teams_and_aliases,
        scoped=False,
    ),
    Check(
        "odds_after_start",
        "odds snapshots",
        "no snapshot or closing line at/after the start",
        check_odds_after_start,
    ),
    Check(
        "odds_unmatched_events",
        "odds events",
        "odds events without a game, by recorded reason",
        check_odds_unmatched_events,
        finding=WARN,
    ),
    Check(
        "odds_events_per_game",
        "games with odds events",
        "at most one odds event per game",
        check_odds_events_per_game,
        finding=WARN,
    ),
    Check(
        "past_games_not_final",
        "games started over a day ago",
        "games that should be final by now",
        check_past_games_not_final,
        finding=WARN,
    ),
    Check(
        "recent_failed_runs",
        "ingestion runs, last {days} days (all)",
        "partial/failed/aborted runs not redone",
        check_recent_failed_runs,
        finding=WARN,
        scoped=False,
    ),
)
CHECK_NAMES = tuple(c.name for c in CHECKS)


def _result(check: Check, outcome: Outcome, scope: Scope, cfg: CheckSettings) -> CheckResult:
    """Turn a check function's outcome into a report row."""
    findings = outcome.failures + outcome.warnings
    if outcome.skipped:
        status = SKIP
    elif outcome.failures:
        status = check.finding
    elif outcome.warnings:
        status = WARN
    else:
        status = PASS
    return CheckResult(
        check.name,
        check.target_for(scope, cfg),
        status,
        outcome.checked,
        len(findings),
        tuple(findings[:MAX_EXAMPLES]),
        check.description,
        outcome.note,
    )


def _run_one(conn: Connection, check: Check, scope: Scope, cfg: CheckSettings) -> CheckResult:
    """Run one check in a savepoint; an exception becomes an 'error' row, never a crash."""
    try:
        with conn.begin_nested():
            outcome = check.function(conn, scope, cfg)
    except Exception as exc:  # report it and keep going with the next check
        return CheckResult(
            check.name,
            check.target_for(scope, cfg),
            ERROR,
            0,
            0,
            (),
            check.description,
            f"check could not run: {error_line(exc)}",
        )
    return _result(check, outcome, scope, cfg)


def run_checks(
    bind: Engine | Connection,
    *,
    season: str | None = None,
    date_et: date | None = None,
    checks: Sequence[str] | None = None,
    settings: CheckSettings | None = None,
) -> QualityReport:
    """Run the named checks (default all) over the scope and return the report; only reads.

    With an Engine the checks share one REPEATABLE READ, READ ONLY transaction (a consistent
    snapshot). With a Connection they run in the caller's transaction, each in a savepoint.
    """
    unknown = sorted(set(checks or ()) - set(CHECK_NAMES))
    if unknown:
        raise ValueError(f"unknown check(s): {', '.join(unknown)}")
    selected = [c for c in CHECKS if checks is None or c.name in checks]
    scope = Scope(season, date_et)
    cfg = settings or CheckSettings()
    if isinstance(bind, Connection):
        results = [_run_one(bind, c, scope, cfg) for c in selected]
    else:
        with (
            bind.connect().execution_options(
                isolation_level="REPEATABLE READ", postgresql_readonly=True
            ) as conn,
            conn.begin(),
        ):
            results = [_run_one(conn, c, scope, cfg) for c in selected]
    return QualityReport(scope, tuple(results))
