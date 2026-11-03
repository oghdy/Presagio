"""compute_features / compute_day: load what was visible at as_of and build FeatureRows.

Time rules, implemented here on their own (design 2-2; presagio.features never imports
presagio.evaluation, design 9-2 Q4 / 9-3 T6; the tests pin the constants to
presagio.evaluation.timing). For a target game g on US Eastern date D(g) at boundary as_of,
a game h may feed g's features only when

    R1  D(h) < D(g)                           (strict; the same date is never used)
    R2  start_time_utc(h) + Delta <= as_of, status_code(h) = 3 (the status code only; the
        status string is never compared), and both team rows exist in team_game_stats_primary

with Delta = the manifest's availability_lag_hours (3 h, D-049 M-10). Every SQL statement
takes as_of and D(g) as bind parameters, and reads games and the view
team_game_stats_primary only (D-028 A-6; no teams, odds or prediction tables). The target
game's own row is read for its schedule (season, type, start, teams, neutral site) only.
Schedule features read every loaded type under R1 alone (the Cup final included, D-049
M-3); result-based features need R2 as well and predicted types only.

compute_day loads the history once for a date and computes every target game of that date
from it, so its rows equal compute_features' row by row (tested).

Design references: 2-2, 2-4, 3-0, 3-1, 8-1 (input_fingerprint), D-035, D-055 item 1 (earlier
seasons are visible), D-063 items 1-2 (Elo from scratch, opening-day regression).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, text

from presagio.features.fv1 import PriorGame, StatLine, TargetGame, compute_values
from presagio.features.manifest import (
    PREDICTED_GAME_TYPES,
    SCHEDULE_GAME_TYPES,
    FeatureVersion,
    feature_version,
)

logger = logging.getLogger("presagio.features")

EASTERN = ZoneInfo("America/New_York")
# status_code of a finished game (schema v1 6-1): the only finished test (never the status string).
FINAL_STATUS = 3
STAT_COLUMNS = (
    "minutes", "pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "ast", "tov",
)  # fmt: skip


class FeatureComputeError(ValueError):
    """The target game cannot be computed (unknown game, teams not set)."""


@dataclass(frozen=True)
class FeatureRow:
    """One computed features row (what store_features writes)."""

    game_id: str
    as_of: datetime
    feature_version: str
    start_time_utc: datetime
    values: dict[str, Any]
    input_fingerprint: dict[str, Any]


@dataclass(frozen=True)
class History:
    """Everything visible for (D(g), as_of): prior games in start order, the first
    regular-season date per season (up to D(g)), the newest visible stat row."""

    prior: tuple[PriorGame, ...]
    first_regular_date: Mapping[str, date]
    max_stats_fetched_at: datetime | None


def et_date(moment: datetime) -> date:
    """D(x): the US Eastern calendar date of an aware timestamp."""
    if moment.tzinfo is None:
        raise ValueError(f"need an aware timestamp, got naive {moment!r}")
    return moment.astimezone(EASTERN).date()


def availability_lag(spec: FeatureVersion) -> timedelta:
    """Delta of rule R2 for a feature version."""
    return timedelta(hours=spec.availability_lag_hours)


def is_visible(
    *,
    start_time_utc: datetime,
    status_code: int,
    has_stats: bool,
    target_date: date,
    as_of: datetime,
    lag: timedelta,
) -> bool:
    """R1 and R2 in Python (the SQL below applies the same tests; the tests compare both)."""
    return (
        et_date(start_time_utc) < target_date
        and start_time_utc + lag <= as_of
        and status_code == FINAL_STATUS
        and has_stats
    )


TARGET_SQL = text(
    """
    SELECT game_id, season, game_type, start_time_utc, is_time_tbd,
           home_team_id, away_team_id, is_neutral
    FROM games
    WHERE game_id = :game_id
    """
)

# Target games of one ET date (predicted types; D(g) = :target_date).
DAY_TARGETS_SQL = text(
    """
    SELECT game_id, season, game_type, start_time_utc, is_time_tbd,
           home_team_id, away_team_id, is_neutral
    FROM games
    WHERE game_type = ANY(:game_types)
      AND (start_time_utc AT TIME ZONE 'America/New_York')::date = :target_date
    ORDER BY start_time_utc, game_id
    """
)

# Rule R1 alone: the schedule before D(g), every loaded type, no score, no status text.
SCHEDULE_SQL = text(
    """
    SELECT game_id, season, game_type, start_time_utc, home_team_id, away_team_id, is_neutral
    FROM games
    WHERE game_type = ANY(:game_types)
      AND (start_time_utc AT TIME ZONE 'America/New_York')::date < :target_date
      AND home_team_id IS NOT NULL AND away_team_id IS NOT NULL
    ORDER BY start_time_utc, game_id
    """
)

# Rules R1 and R2 (time and status parts): finished predicted-type games whose result
# counted as known at as_of, with whatever rows the primary view has for them (the stats
# part of R2, "both rows exist", is applied on the rows in Python).
RESULTS_SQL = text(
    f"""
    SELECT g.game_id, g.home_score, g.away_score, s.team_id, s.fetched_at,
           {", ".join("s." + c for c in STAT_COLUMNS)}
    FROM games g
    LEFT JOIN team_game_stats_primary s ON s.game_id = g.game_id
    WHERE g.game_type = ANY(:game_types)
      AND (g.start_time_utc AT TIME ZONE 'America/New_York')::date < :target_date
      AND g.status_code = {FINAL_STATUS}
      AND g.start_time_utc + :lag <= :as_of
    ORDER BY g.start_time_utc, g.game_id, s.team_id
    """
)

# First regular-season date of each season, among dates up to D(g) (F-15 season_day).
FIRST_REGULAR_SQL = text(
    """
    SELECT season, min((start_time_utc AT TIME ZONE 'America/New_York')::date) AS first_date
    FROM games
    WHERE game_type = 'regular'
      AND (start_time_utc AT TIME ZONE 'America/New_York')::date <= :target_date
    GROUP BY season
    """
)


def _target(row: Mapping[str, Any]) -> TargetGame:
    """TargetGame from a games row (teams must be set)."""
    if row["home_team_id"] is None or row["away_team_id"] is None:
        raise FeatureComputeError(f"game {row['game_id']} has no teams yet")
    return TargetGame(
        game_id=row["game_id"],
        season=row["season"],
        game_type=row["game_type"],
        et_date=et_date(row["start_time_utc"]),
        start_time_utc=row["start_time_utc"],
        home_team_id=row["home_team_id"],
        away_team_id=row["away_team_id"],
        is_neutral=row["is_neutral"],
    )


def load_history(
    conn: Connection, target_date: date, as_of: datetime, spec: FeatureVersion
) -> History:
    """The prior games of (target_date, as_of): R1 schedule, R2 results with their stats."""
    if as_of.tzinfo is None:
        raise ValueError(f"as_of must be an aware timestamp, got {as_of!r}")
    prior: dict[str, PriorGame] = {}
    rows = conn.execute(
        SCHEDULE_SQL, {"game_types": list(SCHEDULE_GAME_TYPES), "target_date": target_date}
    ).mappings()
    for row in rows:
        prior[row["game_id"]] = PriorGame(
            game_id=row["game_id"],
            season=row["season"],
            game_type=row["game_type"],
            et_date=et_date(row["start_time_utc"]),
            start_time_utc=row["start_time_utc"],
            home_team_id=row["home_team_id"],
            away_team_id=row["away_team_id"],
            is_neutral=row["is_neutral"],
        )
    lag = availability_lag(spec)
    params = {
        "game_types": list(PREDICTED_GAME_TYPES),
        "target_date": target_date,
        "lag": lag,
        "as_of": as_of,
    }
    scores: dict[str, tuple[int, int]] = {}
    lines: dict[str, list[tuple[StatLine, datetime]]] = {}
    for row in conn.execute(RESULTS_SQL, params).mappings():
        game_id = row["game_id"]
        scores[game_id] = (row["home_score"], row["away_score"])
        lines.setdefault(game_id, [])
        if row["team_id"] is not None:
            line = StatLine(team_id=row["team_id"], **{c: row[c] for c in STAT_COLUMNS})
            lines[game_id].append((line, row["fetched_at"]))
    max_fetched: datetime | None = None
    for game_id, (home_score, away_score) in scores.items():
        game = prior.get(game_id)
        if game is None:  # not on the R1 schedule (teams unset): cannot be a result
            continue
        assert is_visible(
            start_time_utc=game.start_time_utc,
            status_code=FINAL_STATUS,
            has_stats=True,
            target_date=target_date,
            as_of=as_of,
            lag=lag,
        ), f"SQL returned a game outside R1/R2: {game_id}"
        stats: Mapping[int, StatLine] | None = None
        if len(lines[game_id]) == 2:
            stats = {line.team_id: line for line, _ in lines[game_id]}
            newest = max(fetched for _, fetched in lines[game_id])
            max_fetched = newest if max_fetched is None else max(max_fetched, newest)
        prior[game_id] = replace(
            game, available=True, home_score=home_score, away_score=away_score, stats=stats
        )
    first = {
        row["season"]: row["first_date"]
        for row in conn.execute(FIRST_REGULAR_SQL, {"target_date": target_date}).mappings()
    }
    ordered = tuple(sorted(prior.values(), key=lambda g: (g.start_time_utc, g.game_id)))
    return History(prior=ordered, first_regular_date=first, max_stats_fetched_at=max_fetched)


def _row(
    target: TargetGame, history: History, as_of: datetime, version: str, spec: FeatureVersion
) -> FeatureRow:
    """Values and input fingerprint (design 8-1) of one target game from the history."""
    computed = compute_values(
        target, history.prior, spec, history.first_regular_date.get(target.season)
    )
    newest = history.max_stats_fetched_at
    fingerprint = {
        "feature_version": version,
        "poss_formula": spec.poss_formula,
        "availability_lag_hours": spec.availability_lag_hours,
        "max_stats_fetched_at": None if newest is None else newest.astimezone(UTC).isoformat(),
        "n_prior_games_home": computed.home.with_stats,
        "n_prior_games_away": computed.away.with_stats,
        "input_completeness": {
            "home": {"with_stats": computed.home.with_stats, "finished": computed.home.finished},
            "away": {"with_stats": computed.away.with_stats, "finished": computed.away.finished},
        },
        "elo": spec.elo_constants,
    }
    return FeatureRow(
        game_id=target.game_id,
        as_of=as_of,
        feature_version=version,
        start_time_utc=target.start_time_utc,
        values=computed.values,
        input_fingerprint=fingerprint,
    )


def compute_features(
    conn: Connection, game_id: str, as_of: datetime, version: str = "fv1"
) -> FeatureRow:
    """The features row of one game at as_of under a feature version (read only)."""
    spec = feature_version(version)
    row = conn.execute(TARGET_SQL, {"game_id": game_id}).mappings().first()
    if row is None:
        raise FeatureComputeError(f"unknown game {game_id!r}")
    target = _target(row)
    history = load_history(conn, target.et_date, as_of, spec)
    return _row(target, history, as_of, version, spec)


def compute_day(
    conn: Connection, day: date, as_of: datetime, version: str = "fv1"
) -> list[FeatureRow]:
    """Features rows of every predicted-type game on ET date `day` at as_of (history read
    once); games without teams or with a TBD time are left out (logged)."""
    spec = feature_version(version)
    rows = conn.execute(
        DAY_TARGETS_SQL, {"game_types": list(PREDICTED_GAME_TYPES), "target_date": day}
    ).mappings()
    targets: list[TargetGame] = []
    for row in rows:
        if row["home_team_id"] is None or row["away_team_id"] is None or row["is_time_tbd"]:
            logger.info("features: %s on %s skipped (teams or time TBD)", row["game_id"], day)
            continue
        targets.append(_target(row))
    if not targets:
        return []
    history = load_history(conn, day, as_of, spec)
    return [_row(t, history, as_of, version, spec) for t in targets]
