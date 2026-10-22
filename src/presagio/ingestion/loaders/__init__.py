"""Loaders: source-neutral records -> idempotent clean-table upserts (no HTTP)."""

from presagio.ingestion.loaders.base import UpsertCounts
from presagio.ingestion.loaders.games import (
    ResultLoadResult,
    ScheduleLoadResult,
    apply_results,
    load_season_results,
    load_season_schedule,
    upsert_schedule,
)
from presagio.ingestion.loaders.odds import (
    MappingResult,
    OddsLoadResult,
    load_odds,
    load_odds_events,
    map_odds_events,
    remap_unmatched_events,
    seed_odds_team_aliases,
    store_odds_batch,
)
from presagio.ingestion.loaders.teams import TeamLoadResult, load_teams, upsert_teams

__all__ = [
    "MappingResult",
    "OddsLoadResult",
    "ResultLoadResult",
    "ScheduleLoadResult",
    "TeamLoadResult",
    "UpsertCounts",
    "apply_results",
    "load_odds",
    "load_odds_events",
    "load_season_results",
    "load_season_schedule",
    "load_teams",
    "map_odds_events",
    "remap_unmatched_events",
    "seed_odds_team_aliases",
    "store_odds_batch",
    "upsert_schedule",
    "upsert_teams",
]
