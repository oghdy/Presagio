"""Game tables: games and team_game_stats (schema v1 ch. 6)."""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, Computed, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column

from presagio.db.base import Base, nonnegative
from presagio.db.models.ingestion import SEASON_PATTERN

# The third digit of an NBA game ID encodes the game type (schema v1 6-1).
GAME_TYPE_EXPRESSION = (
    "CASE substr(game_id, 3, 1) "
    "WHEN '1' THEN 'preseason' WHEN '2' THEN 'regular' WHEN '3' THEN 'allstar' "
    "WHEN '4' THEN 'playoffs' WHEN '5' THEN 'playin' WHEN '6' THEN 'cup_final' "
    "END"
)

BOX_SCORE_COUNTS = (
    "pts",
    "fgm",
    "fga",
    "fg3m",
    "fg3a",
    "ftm",
    "fta",
    "oreb",
    "dreb",
    "reb",
    "ast",
    "stl",
    "blk",
    "tov",
    "pf",
)


class Game(Base):
    """One NBA game with its latest known schedule, status and final score."""

    __tablename__ = "games"

    game_id: Mapped[str] = mapped_column(primary_key=True)
    game_type: Mapped[str] = mapped_column(Computed(GAME_TYPE_EXPRESSION, persisted=True))
    season: Mapped[str]
    game_subtype: Mapped[str | None]
    start_time_utc: Mapped[datetime]
    is_time_tbd: Mapped[bool]
    status_code: Mapped[int]
    status_text: Mapped[str]
    postponed_status: Mapped[str | None]
    home_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.team_id"))
    away_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.team_id"))
    home_score: Mapped[int | None]
    away_score: Mapped[int | None]
    is_neutral: Mapped[bool]
    if_necessary: Mapped[bool]
    source: Mapped[str]
    fetched_at: Mapped[datetime]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    raw_response_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("raw_api_responses.raw_response_id")
    )

    __table_args__ = (
        CheckConstraint("game_id ~ '^00[1-6][0-9]{7}$'", name="game_id_format"),
        CheckConstraint(f"season ~ '{SEASON_PATTERN}'", name="season_format"),
        CheckConstraint("status_code IN (1, 2, 3)", name="status_code_valid"),
        nonnegative("home_score"),
        nonnegative("away_score"),
        CheckConstraint(
            "(home_team_id IS NULL) = (away_team_id IS NULL)", name="teams_both_or_none"
        ),
        CheckConstraint(
            "home_team_id IS NULL OR home_team_id <> away_team_id", name="teams_distinct"
        ),
        CheckConstraint("(home_score IS NULL) = (away_score IS NULL)", name="scores_both_or_none"),
        CheckConstraint("status_code = 3 OR home_score IS NULL", name="score_only_when_final"),
        CheckConstraint(
            "status_code <> 3 OR (home_score IS NOT NULL AND home_team_id IS NOT NULL)",
            name="final_has_score_and_teams",
        ),
        CheckConstraint("substr(game_id, 4, 2) = substr(season, 3, 2)", name="game_id_season"),
        Index("ix_games_start", "start_time_utc"),
        Index("ix_games_home_start", "home_team_id", "start_time_utc"),
        Index("ix_games_away_start", "away_team_id", "start_time_utc"),
        Index("ix_games_season_type", "season", "game_type"),
        Index("ix_games_raw", "raw_response_id"),
    )


class TeamGameStat(Base):
    """One team's box-score line for one game, kept per source endpoint."""

    __tablename__ = "team_game_stats"

    game_id: Mapped[str] = mapped_column(ForeignKey("games.game_id"), primary_key=True)
    team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.team_id"), primary_key=True, autoincrement=False
    )
    source: Mapped[str] = mapped_column(primary_key=True)
    opponent_team_id: Mapped[int] = mapped_column(ForeignKey("teams.team_id"))
    is_home: Mapped[bool]
    minutes: Mapped[int]
    pts: Mapped[int]
    fgm: Mapped[int]
    fga: Mapped[int]
    fg3m: Mapped[int]
    fg3a: Mapped[int]
    ftm: Mapped[int]
    fta: Mapped[int]
    oreb: Mapped[int]
    dreb: Mapped[int]
    reb: Mapped[int]
    ast: Mapped[int]
    stl: Mapped[int]
    blk: Mapped[int]
    tov: Mapped[int]
    pf: Mapped[int]
    plus_minus: Mapped[int]
    fetched_at: Mapped[datetime]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    raw_response_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("raw_api_responses.raw_response_id")
    )

    __table_args__ = (
        CheckConstraint(
            "source IN ('leaguegamelog', 'boxscoretraditionalv3')", name="source_valid"
        ),
        CheckConstraint("minutes > 0", name="minutes_positive"),
        *(nonnegative(column) for column in BOX_SCORE_COUNTS),
        CheckConstraint("team_id <> opponent_team_id", name="opponent_distinct"),
        Index("ix_tgs_team", "team_id"),
        Index("ix_tgs_raw", "raw_response_id"),
    )
