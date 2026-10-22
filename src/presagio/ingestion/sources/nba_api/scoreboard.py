"""nba_api ScoreboardV3: one US Eastern date's games, for the daily ingest (P-017, A-2).

The date is always passed explicitly (game_date): the live scoreboard's "today" follows US
Eastern early-morning time and was still the previous day when called at 03:46 ET (research
6), and its meta.time changes on every call, so it would add a raw row per call (A-2).

The scoreboard gives what the 6-1 table's "scoreboardv3" column lists: status, scores, start
time, teams, subtype and flags. It has no seasonYear and no postponedStatus; the season of a
game is computed from its ID and postponed_status is left to the schedule (record None, which
the games loader never writes over an existing value).
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from nba_api.stats.endpoints import scoreboardv3

from presagio.ingestion.client import IngestionClient, RunCounter, nba_api_request
from presagio.ingestion.sources.base import (
    GAME_ID_PATTERN,
    STORED_GAME_TYPES,
    GameRecord,
    Provenance,
    QualityIssue,
    ScheduleBatch,
    SourceFormatError,
    game_type_of,
    require,
    season_of,
)
from presagio.ingestion.sources.nba_api.schedule import (
    LEAGUE_ID,
    STATUS_FINAL,
    TBD_STATUS_TEXT,
    parse_bool,
    parse_utc,
    provenance_of,
    score,
    team_id_or_none,
)

EASTERN = ZoneInfo("America/New_York")
ENDPOINT = "scoreboardv3"
# Same season window as presagio.ingestion.backfill.SEASON_BOUNDARY (Oct 15 to Oct 15).
SEASON_BOUNDARY = (10, 15)


def season_of_et_date(day: date) -> str:
    """Season whose Oct 15 to Oct 15 window contains the ET date (label of a game-less day)."""
    start = day.year if (day.month, day.day) >= SEASON_BOUNDARY else day.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def et_date_bounds(day: date) -> tuple[datetime, datetime]:
    """UTC interval [day 00:00 ET, day+1 00:00 ET); 23 or 25 hours long on DST days."""
    lower = datetime.combine(day, time.min, EASTERN)
    upper = datetime.combine(date.fromordinal(day.toordinal() + 1), time.min, EASTERN)
    return lower.astimezone(UTC), upper.astimezone(UTC)


def parse_scoreboard_game(game: Any) -> GameRecord:
    """One ScoreboardV3 game object -> GameRecord (season from the ID, no postponed status)."""
    game_id = require(game, "gameId", "scoreboard game")
    where = f"scoreboard game {game_id}"
    status_code = require(game, "gameStatus", where)
    if status_code not in (1, 2, 3):
        raise SourceFormatError(f"{where}: unknown gameStatus {status_code!r}")
    status_text = require(game, "gameStatusText", where)
    if not isinstance(status_text, str):
        raise SourceFormatError(f"{where}: gameStatusText {status_text!r} is not text")
    home = require(game, "homeTeam", where)
    away = require(game, "awayTeam", where)
    home_id = team_id_or_none(require(home, "teamId", where), f"{where} homeTeam")
    away_id = team_id_or_none(require(away, "teamId", where), f"{where} awayTeam")
    # Reject here what a games CHECK would reject, so one odd game cannot fail the date.
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
        start_time_utc=parse_utc(require(game, "gameTimeUTC", where), where),
        is_time_tbd=status_text == TBD_STATUS_TEXT,
        status_code=status_code,
        status_text=status_text,
        postponed_status=None,
        home_team_id=home_id,
        away_team_id=away_id,
        # Scheduled and live games: 0 or a running score is not a result (6-1).
        home_score=score(require(home, "score", where), f"{where} homeTeam") if final else None,
        away_score=score(require(away, "score", where), f"{where} awayTeam") if final else None,
        is_neutral=parse_bool(require(game, "isNeutral", where), where),
        if_necessary=parse_bool(require(game, "ifNecessary", where), where),
    )


def parse_scoreboard(data: Any, game_date: date, provenance: Provenance) -> ScheduleBatch:
    """ScoreboardV3 JSON of one ET date -> stored-type games, skipped types, rejected, issues.

    A response for another date is a format error (the whole call is wrong). A game whose
    start time falls outside the date's ET window is kept with a quality issue.
    """
    board = require(data, "scoreboard", ENDPOINT)
    returned = require(board, "gameDate", ENDPOINT)
    if returned != game_date.isoformat():
        raise SourceFormatError(f"{ENDPOINT}: asked for {game_date}, got gameDate {returned!r}")
    lower, upper = et_date_bounds(game_date)
    games: dict[str, GameRecord] = {}
    skipped: Counter[str] = Counter()
    rejected: dict[str, str] = {}
    issues: list[QualityIssue] = []
    for game in require(board, "games", ENDPOINT):
        game_id = require(game, "gameId", "scoreboard game")
        if not isinstance(game_id, str) or not GAME_ID_PATTERN.match(game_id):
            rejected[str(game_id)] = "invalid game id"
            continue
        game_type = game_type_of(game_id)
        if game_type not in STORED_GAME_TYPES:
            skipped[game_type] += 1
            continue
        try:
            record = parse_scoreboard_game(game)
        except SourceFormatError as exc:
            rejected[game_id] = str(exc)
            continue
        if not record.is_time_tbd and not lower <= record.start_time_utc < upper:
            issues.append(
                QualityIssue(
                    "time_outside_date",
                    game_id,
                    f"gameTimeUTC {record.start_time_utc.isoformat()} is not on ET {game_date}",
                )
            )
        if game_id in games:
            # One upsert statement cannot touch the same key twice; the last one wins.
            issues.append(QualityIssue("duplicate_game", game_id, "listed twice; kept last"))
        games[game_id] = record
    for game_id in games:
        rejected.pop(game_id, None)
    seasons = sorted({record.season for record in games.values()})
    if len(seasons) > 1:
        issues.append(QualityIssue("mixed_seasons", game_date.isoformat(), f"{seasons}"))
    season = seasons[0] if seasons else season_of_et_date(game_date)
    return ScheduleBatch(
        season, tuple(games.values()), provenance, dict(skipped), rejected, tuple(issues)
    )


class NbaApiScoreboardSource:
    """ScoreboardV3 for one ET date per call (the daily ingest's schedule and results)."""

    name = "nba_api"

    def __init__(self, client: IngestionClient, league_id: str = LEAGUE_ID) -> None:
        """All calls go through the given client (pacing, retries, raw storage)."""
        self.client = client
        self.league_id = league_id

    def fetch_scoreboard(self, game_date: date, run: RunCounter | None = None) -> ScheduleBatch:
        """One ScoreboardV3(game_date=YYYY-MM-DD) call: every game of that ET date."""
        endpoint = scoreboardv3.ScoreboardV3(
            game_date=game_date.isoformat(), league_id=self.league_id, get_request=False
        )
        result = self.client.fetch(nba_api_request(endpoint), run)
        return parse_scoreboard(result.data, game_date, provenance_of(result))
