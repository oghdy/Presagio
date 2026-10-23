"""nba_api schedule and results: ScheduleLeagueV2 (per season) and LeagueGameLog (D-023).

Both endpoint objects are built with get_request=False and only describe the request; the
call goes through IngestionClient (D-031). Times come from gameDateTimeUTC only: the *Est
fields carry a fake "Z" on US Eastern wall time (D-025, research 3-4).
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

from nba_api.stats.endpoints import leaguegamelog, scheduleleaguev2

from presagio.ingestion.client import FetchResult, IngestionClient, RunCounter, nba_api_request
from presagio.ingestion.sources.base import (
    GAME_ID_PATTERN,
    RESULT_SEASON_TYPES,
    STORED_GAME_TYPES,
    GameRecord,
    GameResultRecord,
    Provenance,
    QualityIssue,
    ResultBatch,
    ScheduleBatch,
    SourceFormatError,
    game_type_of,
    require,
    season_of,
)

LEAGUE_ID = "00"
# Our season types -> LeagueGameLog's SeasonType parameter (research 1).
NBA_SEASON_TYPES = {"regular": "Regular Season", "playin": "PlayIn", "playoffs": "Playoffs"}
STATUS_FINAL = 3
TBD_STATUS_TEXT = "TBD"
# The 2023-24 schedule sends isNeutral=false for all 1,396 games, so its neutral-site games are
# listed here (P-025, D-046), found in raw arenaCity/gameLabel: Mexico City, the cup semifinals
# and final in Las Vegas, Paris. 2022-23 also sends false for all 1,394 games (P-026): Mexico
# City and Paris. From 2024-25 the flag marks every neutral game (Indianapolis hosts the
# 2026-27 cup final, so a host-city list could not), and the past seasons are closed.
# The schedule and the scoreboard both apply it.
UNFLAGGED_NEUTRAL_GAME_IDS = frozenset(
    {"0022300172", "0022301229", "0022301230", "0062300001", "0022300527"}
    | {"0022200439", "0022200678"}
)


def provenance_of(result: FetchResult) -> Provenance:
    """Provenance of a fetched response: endpoint name, receive time, raw row id."""
    return Provenance(result.request.endpoint, result.fetched_at, result.stored.raw_response_id)


def parse_utc(value: Any, where: str) -> datetime:
    """Parse an ISO-8601 time that must carry an offset, returned in UTC."""
    if not isinstance(value, str):
        raise SourceFormatError(f"{where}: expected an ISO time string, got {value!r}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise SourceFormatError(f"{where}: {value!r} is not an ISO time") from None
    if parsed.tzinfo is None:
        raise SourceFormatError(f"{where}: time {value!r} has no UTC offset")
    return parsed.astimezone(UTC)


def parse_bool(value: Any, where: str) -> bool:
    """A boolean that the schedule sends as "true"/"false" strings and others as JSON bools."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    raise SourceFormatError(f"{where}: expected a boolean, got {value!r}")


def is_neutral_game(game_id: str, flag: bool) -> bool:
    """The isNeutral flag, or a known neutral-site game the source does not flag."""
    return flag or game_id in UNFLAGGED_NEUTRAL_GAME_IDS


def team_id_or_none(value: Any, where: str) -> int | None:
    """Team ID, with the source's 0 ("not decided yet", A-5) mapped to None."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise SourceFormatError(f"{where}: expected a team ID, got {value!r}")
    return value or None


def score(value: Any, where: str) -> int:
    """A final score: a non-negative integer (ck_games_* would reject anything else)."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise SourceFormatError(f"{where}: expected a non-negative score, got {value!r}")
    return value


def parse_schedule_game(game: Any) -> GameRecord:
    """One ScheduleLeagueV2 game object -> GameRecord (the caller filters by game type)."""
    game_id = require(game, "gameId", "schedule game")
    where = f"schedule game {game_id}"
    status_code = require(game, "gameStatus", where)
    if status_code not in (1, 2, 3):
        raise SourceFormatError(f"{where}: unknown gameStatus {status_code!r}")
    status_text = require(game, "gameStatusText", where)
    home = require(game, "homeTeam", where)
    away = require(game, "awayTeam", where)
    home_id = team_id_or_none(require(home, "teamId", where), f"{where} homeTeam")
    away_id = team_id_or_none(require(away, "teamId", where), f"{where} awayTeam")
    # Reject here what a games CHECK would reject, so one odd game cannot fail the season.
    if (home_id is None) != (away_id is None):
        raise SourceFormatError(f"{where}: only one team decided ({home_id} vs {away_id})")
    if home_id is not None and home_id == away_id:
        raise SourceFormatError(f"{where}: home and away are the same team {home_id}")
    final = status_code == STATUS_FINAL
    if final and home_id is None:
        raise SourceFormatError(f"{where}: final game without both teams")
    return GameRecord(
        game_id=game_id,
        season=season_of(game_id),
        game_subtype=require(game, "gameSubtype", where) or None,
        start_time_utc=parse_utc(require(game, "gameDateTimeUTC", where), where),
        is_time_tbd=status_text == TBD_STATUS_TEXT,
        status_code=status_code,
        status_text=status_text,
        postponed_status=require(game, "postponedStatus", where),
        home_team_id=home_id,
        away_team_id=away_id,
        # The schedule sends 0 for games not played yet; 0 points and "no score" differ.
        home_score=score(require(home, "score", where), f"{where} homeTeam") if final else None,
        away_score=score(require(away, "score", where), f"{where} awayTeam") if final else None,
        is_neutral=is_neutral_game(game_id, parse_bool(require(game, "isNeutral", where), where)),
        if_necessary=parse_bool(require(game, "ifNecessary", where), where),
    )


def parse_schedule(data: Any, season: str, provenance: Provenance) -> ScheduleBatch:
    """ScheduleLeagueV2 JSON -> stored-type games, skipped types, rejected games and issues."""
    schedule = require(data, "leagueSchedule", "scheduleleaguev2")
    season_year = require(schedule, "seasonYear", "scheduleleaguev2")
    if season_year != season:
        raise SourceFormatError(f"scheduleleaguev2: asked for {season}, got {season_year!r}")
    games: dict[str, GameRecord] = {}
    skipped: Counter[str] = Counter()
    rejected: dict[str, str] = {}
    issues: list[QualityIssue] = []
    for game_date in require(schedule, "gameDates", "scheduleleaguev2"):
        for game in require(game_date, "games", "scheduleleaguev2 gameDate"):
            game_id = require(game, "gameId", "schedule game")
            if not isinstance(game_id, str) or not GAME_ID_PATTERN.match(game_id):
                rejected[str(game_id)] = "invalid game id"
                continue
            game_type = game_type_of(game_id)
            if game_type not in STORED_GAME_TYPES:
                skipped[game_type] += 1
                continue
            try:
                record = parse_schedule_game(game)
            except SourceFormatError as exc:
                rejected[game_id] = str(exc)
                continue
            if record.season != season_year:
                issues.append(
                    QualityIssue(
                        "season_mismatch",
                        game_id,
                        f"ID says {record.season}, seasonYear says {season_year}; ID kept",
                    )
                )
            if game_id in games:
                # One upsert statement cannot touch the same key twice; the last one wins.
                issues.append(QualityIssue("duplicate_game", game_id, "listed twice; kept last"))
            games[game_id] = record
    # A game rejected in one listing but valid in another is stored, so it has not failed.
    for game_id in games:
        rejected.pop(game_id, None)
    return ScheduleBatch(
        season, tuple(games.values()), provenance, dict(skipped), rejected, tuple(issues)
    )


def is_neutral_site(game_rows: list[list[Any]], aways: list[list[Any]], team_col: int) -> bool:
    """Two rows of two different teams, both written as away ('X @ Y'): a neutral-site game."""
    teams = {row[team_col] for row in game_rows}
    return len(game_rows) == 2 and len(aways) == 2 and len(teams) == 2


def parse_game_log(data: Any, season: str, season_type: str, provenance: Provenance) -> ResultBatch:
    """LeagueGameLog (team rows) JSON -> one GameResultRecord per game with two final rows.

    Home and away come from MATCHUP: "DEN vs. LAL" is DEN at home, "LAL @ DEN" is LAL away.
    A neutral-site game (schedule isNeutral: Mexico City, Paris, cup semifinals in Las Vegas)
    lists both teams as away ("IND @ SAS", "SAS @ IND"). Its record is oriented by team ID
    with a 'neutral_site' issue; loaders take home/away from games (the schedule, D-034) and
    match scores and stat lines by team ID, so the orientation never decides anything.
    """
    result_sets = require(data, "resultSets", "leaguegamelog")
    if not result_sets:
        raise SourceFormatError("leaguegamelog: empty resultSets")
    headers = require(result_sets[0], "headers", "leaguegamelog")
    rows = require(result_sets[0], "rowSet", "leaguegamelog")
    needed = ("GAME_ID", "TEAM_ID", "MATCHUP", "WL", "PTS")
    missing = [name for name in needed if name not in headers]
    if missing:
        raise SourceFormatError(f"leaguegamelog: missing columns {missing}")
    index = {name: headers.index(name) for name in needed}
    by_game: dict[str, list[list[Any]]] = {}
    for row in rows:
        by_game.setdefault(row[index["GAME_ID"]], []).append(row)

    results: list[GameResultRecord] = []
    issues: list[QualityIssue] = []
    for game_id, game_rows in by_game.items():
        if not isinstance(game_id, str) or not GAME_ID_PATTERN.match(game_id):
            issues.append(QualityIssue("invalid_game_id", str(game_id), "skipped"))
            continue
        # Our season type names equal the game types (regular, playin, playoffs).
        if game_type_of(game_id) != season_type or season_of(game_id) != season:
            issues.append(
                QualityIssue("unexpected_game", game_id, f"not a {season} {season_type} game")
            )
            continue
        if any(row[index["WL"]] is None or row[index["PTS"]] is None for row in game_rows):
            issues.append(QualityIssue("not_final", game_id, "row without WL or PTS"))
            continue
        homes = [row for row in game_rows if " vs. " in row[index["MATCHUP"]]]
        aways = [row for row in game_rows if " @ " in row[index["MATCHUP"]]]
        if is_neutral_site(game_rows, aways, index["TEAM_ID"]):
            aways = sorted(game_rows, key=lambda row: row[index["TEAM_ID"]])
            homes = [aways.pop()]
            matchups = [row[index["MATCHUP"]] for row in game_rows]
            issues.append(
                QualityIssue(
                    "neutral_site",
                    game_id,
                    f"both rows away {matchups}; home/away taken from the schedule",
                )
            )
        if len(game_rows) != 2 or len(homes) != 1 or len(aways) != 1:
            matchups = [row[index["MATCHUP"]] for row in game_rows]
            issues.append(
                QualityIssue("matchup_ambiguous", game_id, f"cannot tell home/away: {matchups}")
            )
            continue
        home, away = homes[0], aways[0]
        results.append(
            GameResultRecord(
                game_id=game_id,
                home_team_id=home[index["TEAM_ID"]],
                away_team_id=away[index["TEAM_ID"]],
                home_score=home[index["PTS"]],
                away_score=away[index["PTS"]],
            )
        )
    return ResultBatch(season, season_type, tuple(results), provenance, tuple(issues))


class NbaApiScheduleSource:
    """ScheduleSource and ResultSource over nba_api's stats endpoints."""

    name = "nba_api"
    season_type_labels = NBA_SEASON_TYPES

    def __init__(self, client: IngestionClient, league_id: str = LEAGUE_ID) -> None:
        """All calls go through the given client (pacing, retries, raw storage)."""
        self.client = client
        self.league_id = league_id

    def fetch_season_schedule(self, season: str, run: RunCounter | None = None) -> ScheduleBatch:
        """One ScheduleLeagueV2 call for the whole season (preseason to finals)."""
        endpoint = scheduleleaguev2.ScheduleLeagueV2(
            season=season, league_id=self.league_id, get_request=False
        )
        result = self.client.fetch(nba_api_request(endpoint), run)
        return parse_schedule(result.data, season, provenance_of(result))

    def fetch_season_results(
        self, season: str, season_type: str, run: RunCounter | None = None
    ) -> ResultBatch:
        """One LeagueGameLog call (team rows) for the season type."""
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
        return parse_game_log(result.data, season, season_type, provenance_of(result))
