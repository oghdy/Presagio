"""Fixtures of the evaluation tests: the league temp DB (tests/evaluation/league.py).

The league is committed once per session; tests that change data do it on the `conn`
fixture, a transaction that is rolled back afterwards.
"""

from collections.abc import Callable, Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import URL, Connection, Engine

from presagio.db.session import make_engine

from .league import League, load_league


@pytest.fixture(scope="session")
def league_db(
    make_test_database: Callable[[], URL], alembic_config: Callable[[Connection], Config]
) -> Iterator[tuple[Engine, League]]:
    """Fresh temp DB migrated to head with the league committed; dropped after the run."""
    engine = make_engine(make_test_database())
    with engine.begin() as conn:
        command.upgrade(alembic_config(conn), "head")
    league = load_league(engine)
    yield engine, league
    engine.dispose()


@pytest.fixture
def engine(league_db: tuple[Engine, League]) -> Engine:
    """Engine on the league DB."""
    return league_db[0]


@pytest.fixture
def league(league_db: tuple[Engine, League]) -> League:
    """What the league DB holds."""
    return league_db[1]


@pytest.fixture
def conn(engine: Engine) -> Iterator[Connection]:
    """A connection in a transaction that is rolled back after the test."""
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()
