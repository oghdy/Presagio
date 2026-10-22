"""Source-neutral records and the interfaces every team/schedule/result/stats source implements.

An adapter turns one source's responses into these records and knows nothing about the
database; a loader writes the records and knows nothing about HTTP. Swapping nba_api for
another provider (risks B1) means writing a new adapter that satisfies the same protocols.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from presagio.ingestion.client import RunCounter
from presagio.ingestion.errors import IngestionError

# Game IDs are 10-digit strings: "00" + type digit + season start year (2 digits) + number.
GAME_ID_PATTERN = re.compile(r"^00[1-6][0-9]{7}$")
GAME_TYPES = {
    "1": "preseason",
    "2": "regular",
    "3": "allstar",
    "4": "playoffs",
    "5": "playin",
    "6": "cup_final",
}
# Types written to games (D-028 A-4); preseason and all-star games are skipped and counted.
STORED_GAME_TYPES = frozenset({"regular", "playoffs", "playin", "cup_final"})
# Season types a result source can be asked for, named like games.game_type.
RESULT_SEASON_TYPES = ("regular", "playin", "playoffs")


class SourceFormatError(IngestionError):
    """A 2xx response whose structure is not what the adapter expects (no field guessing)."""


def game_type_of(game_id: str) -> str:
    """Game type from the third digit of the ID (D-025), e.g. '0042300111' -> 'playoffs'."""
    if not GAME_ID_PATTERN.match(game_id):
        raise ValueError(f"not a 10-digit NBA game ID: {game_id!r}")
    return GAME_TYPES[game_id[2]]


def season_of(game_id: str) -> str:
    """Season from ID digits 4-5 (schema 6-1), e.g. '0022300061' -> '2023-24'."""
    if not GAME_ID_PATTERN.match(game_id):
        raise ValueError(f"not a 10-digit NBA game ID: {game_id!r}")
    start = 2000 + int(game_id[3:5])
    return f"{start}-{(start + 1) % 100:02d}"


@dataclass(frozen=True)
class QualityIssue:
    """Something a source said that we did not trust or could not use; never an exception."""

    kind: str
    key: str
    message: str

    def as_dict(self) -> dict[str, str]:
        """JSON-ready form for ingestion_runs.error_detail."""
        return {"kind": self.kind, "key": self.key, "message": self.message}


@dataclass(frozen=True)
class Provenance:
    """Where a batch came from: the endpoint name, when we received it, and its raw row."""

    source: str
    fetched_at: datetime
    raw_response_id: int | None


@dataclass(frozen=True)
class TeamRecord:
    """One franchise as the teams table stores it."""

    team_id: int
    tricode: str
    city: str
    nickname: str
    full_name: str


@dataclass(frozen=True)
class TeamAliasRecord:
    """A name some source uses for a team."""

    source: str
    alias: str
    team_id: int


@dataclass(frozen=True)
class TeamBatch:
    """All teams and their aliases from one source, with the time they were confirmed."""

    teams: tuple[TeamRecord, ...]
    aliases: tuple[TeamAliasRecord, ...]
    provenance: Provenance
    issues: tuple[QualityIssue, ...] = ()


@dataclass(frozen=True)
class GameRecord:
    """One game as a schedule source describes it (every column the schedule owns)."""

    game_id: str
    season: str
    game_subtype: str | None
    start_time_utc: datetime
    is_time_tbd: bool
    status_code: int
    status_text: str
    postponed_status: str | None
    home_team_id: int | None
    away_team_id: int | None
    home_score: int | None
    away_score: int | None
    is_neutral: bool
    if_necessary: bool


@dataclass(frozen=True)
class ScheduleBatch:
    """A season schedule: games to store, skipped types, rejected games, and issues.

    skipped counts game types we never store (A-4); rejected maps a game the source listed
    but we could not use to the reason, so the run can report it as a failed item.
    """

    season: str
    games: tuple[GameRecord, ...]
    provenance: Provenance
    skipped: Mapping[str, int] = field(default_factory=dict)
    rejected: Mapping[str, str] = field(default_factory=dict)
    issues: tuple[QualityIssue, ...] = ()


@dataclass(frozen=True)
class GameResultRecord:
    """A final score as a result source reports it, with its own idea of home and away."""

    game_id: str
    home_team_id: int
    away_team_id: int
    home_score: int
    away_score: int


@dataclass(frozen=True)
class ResultBatch:
    """Final scores of one season type."""

    season: str
    season_type: str
    results: tuple[GameResultRecord, ...]
    provenance: Provenance
    issues: tuple[QualityIssue, ...] = ()


@dataclass(frozen=True)
class TeamGameStatRecord:
    """One team's box-score line in one game, values exactly as the source gave them (A-6)."""

    game_id: str
    team_id: int
    opponent_team_id: int
    is_home: bool
    minutes: int
    pts: int
    fgm: int
    fga: int
    fg3m: int
    fg3a: int
    ftm: int
    fta: int
    oreb: int
    dreb: int
    reb: int
    ast: int
    stl: int
    blk: int
    tov: int
    pf: int
    plus_minus: int


@dataclass(frozen=True)
class TeamStatsBatch:
    """Team box-score lines from one response: two per usable game.

    rejected maps a game the source listed but we could not use to the reason (a failed
    item). results carries the final scores of the same response when the source has them
    (LeagueGameLog), so one call feeds both the games loader and the team_game_stats loader.
    """

    stats: tuple[TeamGameStatRecord, ...]
    provenance: Provenance
    season: str | None = None
    season_type: str | None = None
    rejected: Mapping[str, str] = field(default_factory=dict)
    issues: tuple[QualityIssue, ...] = ()
    results: ResultBatch | None = None


@runtime_checkable
class TeamSource(Protocol):
    """Gives the current list of teams."""

    name: str

    def fetch_teams(self, run: RunCounter | None = None) -> TeamBatch:
        """Fetch every active team and its aliases."""
        ...


@runtime_checkable
class ScheduleSource(Protocol):
    """Gives a whole season's schedule, including results of finished games."""

    name: str

    def fetch_season_schedule(self, season: str, run: RunCounter | None = None) -> ScheduleBatch:
        """Fetch every game of the season (e.g. '2025-26')."""
        ...


@runtime_checkable
class ResultSource(Protocol):
    """Gives final scores for one season type, used to fill or confirm schedule results."""

    name: str
    # Our season type -> the source's own label, recorded in ingestion_runs.season_type.
    season_type_labels: Mapping[str, str]

    def fetch_season_results(
        self, season: str, season_type: str, run: RunCounter | None = None
    ) -> ResultBatch:
        """Fetch final scores; season_type is one of RESULT_SEASON_TYPES."""
        ...


@runtime_checkable
class TeamStatsSource(Protocol):
    """Gives every team box-score line of one season type (one call), with final scores."""

    name: str
    # Our season type -> the source's own label, recorded in ingestion_runs.season_type.
    season_type_labels: Mapping[str, str]

    def fetch_season_team_stats(
        self, season: str, season_type: str, run: RunCounter | None = None
    ) -> TeamStatsBatch:
        """Fetch team lines; season_type is one of RESULT_SEASON_TYPES."""
        ...


@runtime_checkable
class GameTeamStatsSource(Protocol):
    """Gives both teams' box-score lines of one game (one call per game)."""

    name: str

    def fetch_game_team_stats(self, game_id: str, run: RunCounter | None = None) -> TeamStatsBatch:
        """Fetch the two team lines of a finished game."""
        ...


def require(mapping: Any, key: str, where: str) -> Any:
    """mapping[key], or SourceFormatError naming the missing field (no silent defaults)."""
    if not isinstance(mapping, Mapping) or key not in mapping:
        raise SourceFormatError(f"{where}: missing field {key!r}")
    return mapping[key]
