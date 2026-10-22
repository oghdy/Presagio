"""Fixtures for loader tests: an empty migrated DB and a client serving nba_api fixtures."""

from collections.abc import Callable
from dataclasses import dataclass, field

import pytest
from sqlalchemy import Engine, text

from presagio.ingestion.client import IngestionClient, db_raw_store
from presagio.ingestion.sources.nba_api import NbaApiScheduleSource, NbaApiTeamSource

from ..helpers import FakeClock, Handler, nba_fixture_handler


@pytest.fixture
def db(engine: Engine) -> Engine:
    """The migrated temp DB with teams and aliases emptied too (engine clears raw/runs/games)."""
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE teams, team_aliases CASCADE"))
    return engine


@dataclass
class NbaApiStub:
    """nba_api sources over fixture responses, storing raw rows in the temp DB."""

    teams: NbaApiTeamSource
    schedule: NbaApiScheduleSource
    handler: Handler
    clock: FakeClock
    overrides: dict[str, bytes] = field(default_factory=dict)


@pytest.fixture
def nba(db: Engine, make_client: Callable[..., IngestionClient], clock: FakeClock) -> NbaApiStub:
    """Adapters -> IngestionClient -> MockTransport(fixtures), raw stored in the temp DB."""
    overrides: dict[str, bytes] = {}
    handler = nba_fixture_handler(overrides)
    client = make_client(handler, store=db_raw_store(db))
    return NbaApiStub(
        NbaApiTeamSource(client), NbaApiScheduleSource(client), handler, clock, overrides
    )
