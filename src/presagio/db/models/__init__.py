"""SQLAlchemy models for schema v1; importing this package registers every table."""

from presagio.db.base import Base
from presagio.db.models.games import Game, TeamGameStat
from presagio.db.models.ingestion import IngestionRun, RawApiResponse
from presagio.db.models.modeling import Evaluation, Feature, ModelVersion, Prediction
from presagio.db.models.odds import OddsEvent, OddsSnapshot
from presagio.db.models.teams import Team, TeamAlias

# Views defined by the migrations (raw SQL); autogenerate must not treat them as tables.
VIEW_NAMES = frozenset(
    {"team_game_stats_primary", "odds_closing_lines", "predictions_recommendation_basis"}
)

__all__ = [
    "VIEW_NAMES",
    "Base",
    "Evaluation",
    "Feature",
    "Game",
    "IngestionRun",
    "ModelVersion",
    "OddsEvent",
    "OddsSnapshot",
    "Prediction",
    "RawApiResponse",
    "Team",
    "TeamAlias",
    "TeamGameStat",
]
