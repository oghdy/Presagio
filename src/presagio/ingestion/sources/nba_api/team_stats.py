"""nba_api team box scores: LeagueGameLog team rows (main, D-023) and BoxScoreTraditionalV3.

LeagueGameLog is the primary team_game_stats source (A-6): one call per season type returns
every team row. The same response also carries the final scores the games loader applies,
so parse_game_log_team_stats parses it once for both (results reuses schedule.parse_game_log).

BoxScoreTraditionalV3 is a per-game fallback (e.g. the 006 cup final, which no game log
lists); nothing calls it by default. Only its team "statistics" block is read: bench.minutes
repeats the starters' value (research 6) and starters/bench/players are not stored in v1.

Values are stored as the source gave them, never reconciled across sources: TOV differs
(game log includes team turnovers, box score sums players). team_game_stats_primary picks.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from nba_api.stats.endpoints import boxscoretraditionalv3, leaguegamelog

from presagio.ingestion.client import IngestionClient, RunCounter, nba_api_request
from presagio.ingestion.sources.base import (
    GAME_ID_PATTERN,
    RESULT_SEASON_TYPES,
    Provenance,
    SourceFormatError,
    TeamGameStatRecord,
    TeamStatsBatch,
    require,
    season_of,
)
from presagio.ingestion.sources.nba_api.schedule import (
    LEAGUE_ID,
    NBA_SEASON_TYPES,
    parse_game_log,
    provenance_of,
)

# Record field -> (LeagueGameLog column, BoxScoreTraditionalV3 statistics key), schema 6-2.
COUNT_FIELDS: Mapping[str, tuple[str, str]] = {
    "pts": ("PTS", "points"),
    "fgm": ("FGM", "fieldGoalsMade"),
    "fga": ("FGA", "fieldGoalsAttempted"),
    "fg3m": ("FG3M", "threePointersMade"),
    "fg3a": ("FG3A", "threePointersAttempted"),
    "ftm": ("FTM", "freeThrowsMade"),
    "fta": ("FTA", "freeThrowsAttempted"),
    "oreb": ("OREB", "reboundsOffensive"),
    "dreb": ("DREB", "reboundsDefensive"),
    "reb": ("REB", "reboundsTotal"),
    "ast": ("AST", "assists"),
    "stl": ("STL", "steals"),
    "blk": ("BLK", "blocks"),
    "tov": ("TOV", "turnovers"),
    "pf": ("PF", "foulsPersonal"),
}
GAME_LOG_COLUMNS = (
    "GAME_ID",
    "TEAM_ID",
    "MIN",
    "PLUS_MINUS",
    *(column for column, _ in COUNT_FIELDS.values()),
)
# parse_game_log issues that mean "a game of this season type we could not use": failed
# items for team stats. unexpected_game (another season or type) is not ours to load.
UNUSABLE_GAME_ISSUES = frozenset({"invalid_game_id", "not_final", "matchup_ambiguous"})
# Box score team minutes, e.g. "240:00" or "265:00".
BOX_MINUTES = re.compile(r"^(\d+):(\d{2})$")


def valid_team_id(value: Any, where: str) -> int:
    """A decided team's ID: a positive integer."""
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise SourceFormatError(f"{where}: expected a team ID, got {value!r}")
    return value


def count(value: Any, where: str) -> int:
    """A box-score count: a non-negative integer (ck_team_game_stats_* reject others)."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise SourceFormatError(f"{where}: expected a non-negative integer, got {value!r}")
    return value


def whole_number(value: Any, where: str) -> int:
    """An integer that a source may send as a float (plusMinusPoints 12.0); 12.5 is refused."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SourceFormatError(f"{where}: expected a number, got {value!r}")
    if isinstance(value, float) and not value.is_integer():
        raise SourceFormatError(f"{where}: {value!r} is not a whole number")
    return int(value)


def positive_minutes(value: Any, where: str) -> int:
    """Team minutes: a positive integer (ck_team_game_stats_minutes_positive)."""
    minutes = count(value, where)
    if minutes == 0:
        raise SourceFormatError(f"{where}: team minutes are 0")
    return minutes


def box_minutes(value: Any, where: str) -> int:
    """Box score "240:00" -> 240; seconds other than 00 are refused (schema 6-2)."""
    match = BOX_MINUTES.match(value) if isinstance(value, str) else None
    if match is None:
        raise SourceFormatError(f"{where}: minutes {value!r} are not 'MMM:SS'")
    if match.group(2) != "00":
        raise SourceFormatError(f"{where}: team minutes {value!r} are not whole minutes")
    return positive_minutes(int(match.group(1)), where)


def game_log_line(
    row: list[Any], index: Mapping[str, int], game_id: str, opponent_id: int, is_home: bool
) -> TeamGameStatRecord:
    """One LeagueGameLog team row -> TeamGameStatRecord."""
    team_id = row[index["TEAM_ID"]]
    where = f"leaguegamelog game {game_id} team {team_id}"
    return TeamGameStatRecord(
        game_id=game_id,
        team_id=team_id,
        opponent_team_id=opponent_id,
        is_home=is_home,
        minutes=positive_minutes(row[index["MIN"]], f"{where} MIN"),
        plus_minus=whole_number(row[index["PLUS_MINUS"]], f"{where} PLUS_MINUS"),
        **{
            name: count(row[index[column]], f"{where} {column}")
            for name, (column, _) in COUNT_FIELDS.items()
        },
    )


def parse_game_log_team_stats(
    data: Any, season: str, season_type: str, provenance: Provenance
) -> TeamStatsBatch:
    """LeagueGameLog (team rows) JSON -> two team lines per usable game, plus its results.

    A game is usable when parse_game_log found exactly one home and one away final row and
    every stat value is valid. Otherwise the whole game is rejected (never one team alone),
    so a stored game always has both lines.
    """
    results = parse_game_log(data, season, season_type, provenance)
    result_set = data["resultSets"][0]
    headers = require(result_set, "headers", "leaguegamelog")
    missing = [name for name in GAME_LOG_COLUMNS if name not in headers]
    if missing:
        raise SourceFormatError(f"leaguegamelog: missing columns {missing}")
    index = {name: headers.index(name) for name in GAME_LOG_COLUMNS}
    rows_by_game: dict[str, list[list[Any]]] = {}
    for row in result_set["rowSet"]:
        rows_by_game.setdefault(row[index["GAME_ID"]], []).append(row)

    stats: list[TeamGameStatRecord] = []
    rejected = {
        issue.key: f"{issue.kind}: {issue.message}"
        for issue in results.issues
        if issue.kind in UNUSABLE_GAME_ISSUES
    }
    for result in results.results:
        home_id, away_id = result.home_team_id, result.away_team_id
        rows = {row[index["TEAM_ID"]]: row for row in rows_by_game[result.game_id]}
        try:
            for team_id in (home_id, away_id):
                valid_team_id(team_id, f"leaguegamelog game {result.game_id} TEAM_ID")
            if home_id == away_id:
                raise SourceFormatError(f"leaguegamelog game {result.game_id}: same team twice")
            lines = (
                game_log_line(rows[home_id], index, result.game_id, away_id, is_home=True),
                game_log_line(rows[away_id], index, result.game_id, home_id, is_home=False),
            )
        except SourceFormatError as exc:
            rejected[result.game_id] = str(exc)
            continue
        stats.extend(lines)
    return TeamStatsBatch(
        stats=tuple(stats),
        provenance=provenance,
        season=season,
        season_type=season_type,
        rejected=rejected,
        results=results,
    )


def box_score_line(team: Any, game_id: str, opponent_id: int, is_home: bool) -> TeamGameStatRecord:
    """One BoxScoreTraditionalV3 homeTeam/awayTeam object -> TeamGameStatRecord."""
    team_id = require(team, "teamId", f"boxscoretraditionalv3 game {game_id}")
    where = f"boxscoretraditionalv3 game {game_id} team {team_id}"
    statistics = require(team, "statistics", where)
    return TeamGameStatRecord(
        game_id=game_id,
        team_id=team_id,
        opponent_team_id=opponent_id,
        is_home=is_home,
        minutes=box_minutes(require(statistics, "minutes", where), f"{where} minutes"),
        plus_minus=whole_number(
            require(statistics, "plusMinusPoints", where), f"{where} plusMinusPoints"
        ),
        **{
            name: count(require(statistics, key, where), f"{where} {key}")
            for name, (_, key) in COUNT_FIELDS.items()
        },
    )


def team_id_of(team: Any, where: str) -> int:
    """The teamId of a box score team object."""
    return valid_team_id(require(team, "teamId", where), where)


def parse_box_score(data: Any, game_id: str, provenance: Provenance) -> TeamStatsBatch:
    """BoxScoreTraditionalV3 JSON -> the two team lines, or the game rejected with a reason.

    A response for another game is a format error (the whole call is wrong); bad values
    inside the right game reject that game only.
    """
    box = require(data, "boxScoreTraditional", "boxscoretraditionalv3")
    returned = require(box, "gameId", "boxscoretraditionalv3")
    if returned != game_id:
        raise SourceFormatError(f"boxscoretraditionalv3: asked for {game_id}, got {returned!r}")
    where = f"boxscoretraditionalv3 game {game_id}"
    try:
        home = require(box, "homeTeam", where)
        away = require(box, "awayTeam", where)
        home_id = team_id_of(home, f"{where} homeTeam")
        away_id = team_id_of(away, f"{where} awayTeam")
        if home_id == away_id:
            raise SourceFormatError(f"{where}: home and away are the same team {home_id}")
        lines = (
            box_score_line(home, game_id, away_id, is_home=True),
            box_score_line(away, game_id, home_id, is_home=False),
        )
    except SourceFormatError as exc:
        return TeamStatsBatch((), provenance, season_of(game_id), rejected={game_id: str(exc)})
    return TeamStatsBatch(lines, provenance, season_of(game_id))


class NbaApiTeamStatsSource:
    """TeamStatsSource over LeagueGameLog team rows (one call per season type)."""

    name = "nba_api"
    season_type_labels = NBA_SEASON_TYPES

    def __init__(self, client: IngestionClient, league_id: str = LEAGUE_ID) -> None:
        """All calls go through the given client (pacing, retries, raw storage)."""
        self.client = client
        self.league_id = league_id

    def fetch_season_team_stats(
        self, season: str, season_type: str, run: RunCounter | None = None
    ) -> TeamStatsBatch:
        """One LeagueGameLog call: team lines and the final scores of the same response."""
        if season_type not in RESULT_SEASON_TYPES:
            raise ValueError(f"season_type must be one of {RESULT_SEASON_TYPES}")
        endpoint = leaguegamelog.LeagueGameLog(
            season=season,
            season_type_all_star=NBA_SEASON_TYPES[season_type],
            player_or_team_abbreviation="T",
            league_id=self.league_id,
            get_request=False,
        )
        result = self.client.fetch(nba_api_request(endpoint), run)
        return parse_game_log_team_stats(result.data, season, season_type, provenance_of(result))


class NbaApiBoxScoreSource:
    """GameTeamStatsSource over BoxScoreTraditionalV3 (one call per game, opt-in only)."""

    name = "nba_api"

    def __init__(self, client: IngestionClient) -> None:
        """All calls go through the given client (pacing, retries, raw storage)."""
        self.client = client

    def fetch_game_team_stats(self, game_id: str, run: RunCounter | None = None) -> TeamStatsBatch:
        """One BoxScoreTraditionalV3 call for a finished game."""
        if not GAME_ID_PATTERN.match(game_id):
            raise ValueError(f"not a 10-digit NBA game ID: {game_id!r}")
        endpoint = boxscoretraditionalv3.BoxScoreTraditionalV3(game_id=game_id, get_request=False)
        result = self.client.fetch(nba_api_request(endpoint), run)
        return parse_box_score(result.data, game_id, provenance_of(result))
