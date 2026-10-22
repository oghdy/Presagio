"""Odds adapters: provider responses -> source-neutral OddsBatch records (no database access)."""

from presagio.ingestion.sources.odds.base import (
    CAPTURE_KINDS,
    MARKETS,
    SIDES_BY_MARKET,
    OddsBatch,
    OddsEventRecord,
    OddsOutcomeRecord,
    OddsSource,
)
from presagio.ingestion.sources.odds.fixture_source import FixtureOddsSource
from presagio.ingestion.sources.odds.the_odds_api import TheOddsApiSource, parse_odds

__all__ = [
    "CAPTURE_KINDS",
    "MARKETS",
    "SIDES_BY_MARKET",
    "FixtureOddsSource",
    "OddsBatch",
    "OddsEventRecord",
    "OddsOutcomeRecord",
    "OddsSource",
    "TheOddsApiSource",
    "parse_odds",
]
