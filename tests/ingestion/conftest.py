"""Fixtures for ingestion tests: fake clock, mock HTTP client, migrated temp DB."""

from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import URL, Connection, Engine, text

from presagio.db.session import make_engine
from presagio.ingestion.client import HttpxTransport, IngestionClient, RequestPolicy

from .helpers import POLICY, FakeClock, FakeStore, Handler


@pytest.fixture
def clock() -> FakeClock:
    """A fresh fake clock."""
    return FakeClock()


@pytest.fixture
def make_client(clock: FakeClock) -> Callable[..., IngestionClient]:
    """Build an IngestionClient over a MockTransport handler, with fake time."""

    def _make(handler: Handler, store: Any = None, policy: RequestPolicy = POLICY) -> Any:
        """Client whose HTTP goes to the handler and whose sleeps are instant."""
        transport = HttpxTransport(httpx.Client(transport=httpx.MockTransport(handler)))
        return IngestionClient(
            store if store is not None else FakeStore(),
            transport,
            policy,
            clock=clock,
            sleep=clock.sleep,
            now=clock.now,
        )

    return _make


@pytest.fixture(scope="module")
def migrated_engine(
    make_test_database: Callable[[], URL], alembic_config: Callable[[Connection], Config]
) -> Iterator[Engine]:
    """Engine on a fresh temp database migrated to head (D-030), dropped after the session."""
    engine = make_engine(make_test_database())
    with engine.begin() as conn:
        command.upgrade(alembic_config(conn), "head")
    yield engine
    engine.dispose()


@pytest.fixture
def engine(migrated_engine: Engine) -> Engine:
    """The migrated engine with both ingestion tables emptied (the code under test commits)."""
    with migrated_engine.begin() as conn:
        conn.execute(text("TRUNCATE raw_api_responses, ingestion_runs RESTART IDENTITY CASCADE"))
    return migrated_engine
