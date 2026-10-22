"""SQLAlchemy models for schema v1; importing this package registers every table."""

from presagio.db.base import Base
from presagio.db.models.games import Game, TeamGameStat
from presagio.db.models.ingestion import IngestionRun, RawApiResponse
from presagio.db.models.odds import OddsEvent, OddsSnapshot
from presagio.db.models.teams import Team, TeamAlias

# Views defined by the initial migration (raw SQL); autogenerate must not treat them as tables.
VIEW_NAMES = frozenset({"team_game_stats_primary", "odds_closing_lines"})

__all__ = [
    "VIEW_NAMES",
    "Base",
    "Game",
    "IngestionRun",
    "OddsEvent",
    "OddsSnapshot",
    "RawApiResponse",
    "Team",
    "TeamAlias",
    "TeamGameStat",
]
