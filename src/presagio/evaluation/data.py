"""Read-only database access of the evaluation framework (no writes; storage is P-033).

Reads games, the view team_game_stats_primary (only whether both team rows exist; feature
values are P-035), odds_events + odds_snapshots for prediction-time odds (rule R3, as_of is a
bind parameter) and, for market_status, team_aliases to recognise unmatched events. The
closing view odds_closing_lines is read by presagio.evaluation.clv only.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import Connection, text

from presagio.evaluation.market import MAX_MARKET_AGE, Quote
from presagio.evaluation.timing import et_date

# Game types the framework reads: the three it predicts (D-028 A-4) plus cup_final, which
# only schedule features use (D-049 M-3) and which the runner never predicts or scores.
LOADED_GAME_TYPES = ("regular", "playin", "playoffs", "cup_final")
# D-036: an odds event maps to a game whose start is within 6 hours of commence_time.
EVENT_MATCH_WINDOW = timedelta(hours=6)


@dataclass(frozen=True)
class GameRecord:
    """One games row as of now (latest schedule and result), plus stat-row presence."""

    game_id: str
    season: str
    game_type: str
    start_time_utc: datetime
    is_time_tbd: bool
    status_code: int
    home_team_id: int | None
    away_team_id: int | None
    home_score: int | None
    away_score: int | None
    is_neutral: bool
    fetched_at: datetime
    has_stats: bool  # both team rows exist in team_game_stats_primary
    et_date: date


GAMES_SQL = text(
    """
    SELECT g.game_id, g.season, g.game_type, g.start_time_utc, g.is_time_tbd, g.status_code,
           g.home_team_id, g.away_team_id, g.home_score, g.away_score, g.is_neutral,
           g.fetched_at, coalesce(s.team_rows, 0) = 2 AS has_stats
    FROM games g
    LEFT JOIN (
        SELECT game_id, count(*) AS team_rows FROM team_game_stats_primary GROUP BY game_id
    ) s USING (game_id)
    WHERE g.game_type = ANY(:game_types)
    ORDER BY g.start_time_utc, g.game_id
    """
)


def load_games(conn: Connection) -> list[GameRecord]:
    """Every loaded-type game in start order (all seasons: earlier ones feed state)."""
    rows = conn.execute(GAMES_SQL, {"game_types": list(LOADED_GAME_TYPES)}).mappings()
    return [GameRecord(**row, et_date=et_date(row["start_time_utc"])) for row in rows]


PREDICTION_QUOTES_SQL = text(
    """
    SELECT e.game_id, s.odds_snapshot_id, s.odds_event_id, s.bookmaker_key, s.market_key,
           s.outcome_side, s.price_decimal, s.point, s.captured_at
    FROM odds_snapshots s
    JOIN odds_events e USING (odds_event_id)
    WHERE e.game_id = ANY(:game_ids)
      AND s.captured_at <= :as_of
      AND s.captured_at >= :oldest
    ORDER BY e.game_id, s.captured_at, s.odds_snapshot_id
    """
)


def load_prediction_quotes(
    conn: Connection,
    game_ids: Sequence[str],
    as_of: datetime,
    max_age: timedelta = MAX_MARKET_AGE,
) -> dict[str, list[Quote]]:
    """Rule R3 window: quotes of the games with as_of - max_age <= captured_at <= as_of."""
    by_game: dict[str, list[Quote]] = {}
    if not game_ids:
        return by_game
    params = {"game_ids": list(game_ids), "as_of": as_of, "oldest": as_of - max_age}
    for row in conn.execute(PREDICTION_QUOTES_SQL, params).mappings():
        by_game.setdefault(row["game_id"], []).append(
            Quote(
                odds_snapshot_id=row["odds_snapshot_id"],
                odds_event_id=row["odds_event_id"],
                bookmaker_key=row["bookmaker_key"],
                market_key=row["market_key"],
                outcome_side=row["outcome_side"],
                price_decimal=row["price_decimal"],
                point=row["point"],
                captured_at=row["captured_at"],
            )
        )
    return by_game


MATCHED_GAMES_SQL = text("SELECT DISTINCT game_id FROM odds_events WHERE game_id = ANY(:game_ids)")

# An unmatched event (game_id NULL) whose two team names are our aliases of the game's two
# teams (either orientation: home_away_swapped is a mapping failure too, D-036) and whose
# commence_time is within the D-036 window. Events with unknown aliases are not found here.
UNMATCHED_CANDIDATES_SQL = text(
    """
    SELECT DISTINCT g.game_id
    FROM games g
    JOIN odds_events e
      ON e.game_id IS NULL
     AND e.commence_time BETWEEN g.start_time_utc - :window AND g.start_time_utc + :window
    JOIN team_aliases ah ON ah.source = 'the_odds_api' AND ah.alias = e.home_team_name
    JOIN team_aliases aa ON aa.source = 'the_odds_api' AND aa.alias = e.away_team_name
    WHERE g.game_id = ANY(:game_ids)
      AND ((ah.team_id = g.home_team_id AND aa.team_id = g.away_team_id)
        OR (ah.team_id = g.away_team_id AND aa.team_id = g.home_team_id))
    """
)


def load_event_matching(
    conn: Connection, game_ids: Sequence[str]
) -> tuple[frozenset[str], frozenset[str]]:
    """(games with a mapped odds event, games with an unmatched candidate event)."""
    if not game_ids:
        return frozenset(), frozenset()
    ids = {"game_ids": list(game_ids)}
    matched = frozenset(conn.execute(MATCHED_GAMES_SQL, ids).scalars())
    candidates = frozenset(
        conn.execute(UNMATCHED_CANDIDATES_SQL, {**ids, "window": EVENT_MATCH_WINDOW}).scalars()
    )
    return matched, candidates
