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
from presagio.ingestion.loaders.teams import TeamLoadResult, load_teams, upsert_teams

__all__ = [
    "ResultLoadResult",
    "ScheduleLoadResult",
    "TeamLoadResult",
    "UpsertCounts",
    "apply_results",
    "load_season_results",
    "load_season_schedule",
    "load_teams",
    "upsert_schedule",
    "upsert_teams",
]
