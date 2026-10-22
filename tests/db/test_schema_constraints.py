"""The migrated schema has the design's keys, indexes and CHECKs, and they reject bad rows."""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import URL, Connection, Engine, insert, select, text
from sqlalchemy.exc import IntegrityError

from presagio.db.models import (
    Game,
    IngestionRun,
    OddsEvent,
    OddsSnapshot,
    RawApiResponse,
    Team,
    TeamGameStat,
)
from presagio.db.models.games import BOX_SCORE_COUNTS
from presagio.db.session import make_engine

# Unique keys from docs/design/schema-v1.md (chapters 3-7, appendix A).
EXPECTED_UNIQUE = {
    "uq_raw_request_content": ("raw_api_responses", "UNIQUE (request_hash, response_sha256)"),
    "uq_teams_tricode": ("teams", "UNIQUE (tricode)"),
    "uq_odds_events_source_id": ("odds_events", "UNIQUE (source, source_event_id)"),
    "uq_odds_snapshot": (
        "odds_snapshots",
        "UNIQUE (odds_event_id, market_key, bookmaker_key, outcome_side, captured_at)",
    ),
}
EXPECTED_PRIMARY_KEYS = {
    "ingestion_runs": "PRIMARY KEY (run_id)",
    "raw_api_responses": "PRIMARY KEY (raw_response_id)",
    "teams": "PRIMARY KEY (team_id)",
    "team_aliases": "PRIMARY KEY (source, alias)",
    "games": "PRIMARY KEY (game_id)",
    "team_game_stats": "PRIMARY KEY (game_id, team_id, source)",
    "odds_events": "PRIMARY KEY (odds_event_id)",
    "odds_snapshots": "PRIMARY KEY (odds_snapshot_id)",
}
# Secondary indexes, as pg_indexes prints them (name, table, "USING btree ..." part).
EXPECTED_INDEXES = {
    "uq_ingestion_runs_one_running": (
        "ingestion_runs",
        "UNIQUE",
        "(job_type, scope_key) WHERE (status = 'running'::text)",
    ),
    "ix_ingestion_runs_resume": (
        "ingestion_runs",
        "",
        "(job_type, scope_key) WHERE (status = 'succeeded'::text)",
    ),
    "ix_ingestion_runs_started": ("ingestion_runs", "", "(started_at DESC)"),
    "ix_ingestion_runs_parent": ("ingestion_runs", "", "(parent_run_id)"),
    "ix_raw_latest": ("raw_api_responses", "", "(request_hash, last_fetched_at DESC)"),
    "ix_raw_source_endpoint": ("raw_api_responses", "", "(source, endpoint, fetched_at DESC)"),
    "ix_raw_run": ("raw_api_responses", "", "(ingestion_run_id)"),
    "ix_team_aliases_team": ("team_aliases", "", "(team_id)"),
    "ix_games_start": ("games", "", "(start_time_utc)"),
    "ix_games_home_start": ("games", "", "(home_team_id, start_time_utc)"),
    "ix_games_away_start": ("games", "", "(away_team_id, start_time_utc)"),
    "ix_games_season_type": ("games", "", "(season, game_type)"),
    "ix_games_raw": ("games", "", "(raw_response_id)"),
    "ix_tgs_team": ("team_game_stats", "", "(team_id)"),
    "ix_tgs_raw": ("team_game_stats", "", "(raw_response_id)"),
    "ix_odds_events_game": ("odds_events", "", "(game_id)"),
    "ix_odds_events_unmatched": ("odds_events", "", "(commence_time) WHERE (game_id IS NULL)"),
    "ix_odds_snapshots_captured": ("odds_snapshots", "", "(captured_at)"),
    "ix_odds_snapshots_raw": ("odds_snapshots", "", "(raw_response_id)"),
}
# Number of CHECK constraints per table in appendix A (60 in total).
EXPECTED_CHECK_COUNTS = {
    "ingestion_runs": 10,
    "raw_api_responses": 8,
    "teams": 2,
    "games": 11,
    "team_game_stats": 18,
    "odds_events": 2,
    "odds_snapshots": 9,
}

T0 = datetime(2026, 10, 22, 23, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def schema_engine(
    make_test_database: Callable[[], URL], alembic_config: Callable[[Connection], Config]
) -> Iterator[Engine]:
    """Engine on a fresh database migrated to head (dropped again after the test session)."""
    engine = make_engine(make_test_database())
    with engine.begin() as conn:
        command.upgrade(alembic_config(conn), "head")
    yield engine
    engine.dispose()


@pytest.fixture
def conn(schema_engine: Engine) -> Iterator[Connection]:
    """Connection whose writes are rolled back after each test, so reruns start clean."""
    with schema_engine.connect() as connection:
        transaction = connection.begin()
        yield connection
        transaction.rollback()


def _violation(conn: Connection, statement: Any, params: dict[str, Any]) -> str:
    """Run a statement that must fail and return the violated constraint's name."""
    with pytest.raises(IntegrityError) as excinfo, conn.begin_nested():
        conn.execute(statement, params)
    return excinfo.value.orig.diag.constraint_name


def _raw_row(**overrides: Any) -> dict[str, Any]:
    """A valid raw_api_responses row."""
    row = {
        "source": "the_odds_api",
        "endpoint": "v4/sports/basketball_nba/odds",
        "request_params": {"markets": "h2h"},
        "request_hash": "a" * 64,
        "http_status": 200,
        "response_json": [],
        "response_sha256": "b" * 64,
        "response_bytes": 2,
        "fetched_at": T0,
        "last_fetched_at": T0,
    }
    return row | overrides


def _seed_event(conn: Connection) -> tuple[int, int]:
    """Insert one raw response, two teams, a game and an odds event; return their ids."""
    raw_id = conn.execute(
        insert(RawApiResponse).returning(RawApiResponse.raw_response_id), _raw_row()
    ).scalar_one()
    conn.execute(
        insert(Team),
        [
            {"team_id": tid, "tricode": code, "city": code, "nickname": code, "full_name": code}
            | {"source": "nba_api.static", "fetched_at": T0}
            for tid, code in ((1610612747, "LAL"), (1610612744, "GSW"))
        ],
    )
    conn.execute(
        insert(Game),
        {
            "game_id": "0022600001",
            "season": "2026-27",
            "start_time_utc": T0 + timedelta(hours=1),
            "is_time_tbd": False,
            "status_code": 1,
            "status_text": "7:00 pm ET",
            "home_team_id": 1610612747,
            "away_team_id": 1610612744,
            "is_neutral": False,
            "if_necessary": False,
            "source": "scheduleleaguev2",
            "fetched_at": T0,
            "raw_response_id": raw_id,
        },
    )
    event_id = conn.execute(
        insert(OddsEvent).returning(OddsEvent.odds_event_id),
        {
            "source": "the_odds_api",
            "source_event_id": "evt-1",
            "home_team_name": "Los Angeles Lakers",
            "away_team_name": "Golden State Warriors",
            "commence_time": T0 + timedelta(hours=1),
            "game_id": "0022600001",
            "match_method": "alias+time",
            "matched_at": T0,
            "fetched_at": T0,
            "raw_response_id": raw_id,
        },
    ).scalar_one()
    return raw_id, event_id


def _snapshot(raw_id: int, event_id: int, captured_at: datetime) -> dict[str, Any]:
    """A valid h2h home-price snapshot captured at captured_at."""
    return {
        "odds_event_id": event_id,
        "source": "the_odds_api",
        "captured_at": captured_at,
        "capture_kind": "live",
        "commence_time": T0 + timedelta(hours=1),
        "bookmaker_key": "pinnacle",
        "bookmaker_last_update": captured_at,
        "market_key": "h2h",
        "outcome_name": "Los Angeles Lakers",
        "outcome_side": "home",
        "price_decimal": "1.9100",
        "point": None,
        "fetched_at": captured_at,
        "raw_response_id": raw_id,
    }


def test_unique_constraints_and_primary_keys_exist(conn: Connection) -> None:
    """Every unique key and primary key in the design is a real constraint on its table."""
    rows = conn.execute(
        text(
            "SELECT conname, conrelid::regclass::text, contype, pg_get_constraintdef(oid) "
            "FROM pg_constraint WHERE connamespace = 'public'::regnamespace "
            "AND contype IN ('u', 'p') AND conrelid::regclass::text <> 'alembic_version'"
        )
    ).all()
    unique = {name: (table, definition) for name, table, kind, definition in rows if kind == "u"}
    primary = {table: definition for _, table, kind, definition in rows if kind == "p"}
    assert unique == EXPECTED_UNIQUE
    assert primary == EXPECTED_PRIMARY_KEYS


def test_indexes_exist(conn: Connection) -> None:
    """Every index in the design exists with the same columns, order and partial predicate."""
    rows = conn.execute(
        text("SELECT indexname, tablename, indexdef FROM pg_indexes WHERE schemaname = 'public'")
    ).all()
    actual = {name: (table, definition) for name, table, definition in rows}
    for name, (table, unique, columns) in EXPECTED_INDEXES.items():
        create = "CREATE UNIQUE INDEX" if unique else "CREATE INDEX"
        assert actual[name] == (table, f"{create} {name} ON public.{table} USING btree {columns}")


def test_check_constraints_exist(conn: Connection) -> None:
    """Each table carries the design's number of CHECK constraints, named ck_<table>_<rule>."""
    rows = conn.execute(
        text(
            "SELECT conrelid::regclass::text, conname FROM pg_constraint "
            "WHERE connamespace = 'public'::regnamespace AND contype = 'c'"
        )
    ).all()
    counts: dict[str, int] = {}
    for table, name in rows:
        assert name.startswith(f"ck_{table}_")
        counts[table] = counts.get(table, 0) + 1
    assert counts == EXPECTED_CHECK_COUNTS


def test_raw_same_request_and_content_is_rejected_twice(conn: Connection) -> None:
    """A second raw row with the same request hash and content hash violates the unique key."""
    conn.execute(insert(RawApiResponse), _raw_row())
    assert _violation(conn, insert(RawApiResponse), _raw_row()) == "uq_raw_request_content"
    # Same request, new content is a new version and is accepted.
    conn.execute(insert(RawApiResponse), _raw_row(response_sha256="c" * 64))
    both_bodies = _raw_row(response_sha256="d" * 64, response_text="[]")
    assert (
        _violation(conn, insert(RawApiResponse), both_bodies)
        == "ck_raw_api_responses_exactly_one_body"
    )


def test_odds_snapshot_duplicate_and_post_tipoff_are_rejected(conn: Connection) -> None:
    """The snapshot key rejects a repeat, and a price captured at or after tip-off is refused."""
    raw_id, event_id = _seed_event(conn)
    before = T0 + timedelta(minutes=30)
    conn.execute(insert(OddsSnapshot), _snapshot(raw_id, event_id, before))
    duplicate = _snapshot(raw_id, event_id, before)
    assert _violation(conn, insert(OddsSnapshot), duplicate) == "uq_odds_snapshot"
    at_tipoff = _snapshot(raw_id, event_id, T0 + timedelta(hours=1))
    assert (
        _violation(conn, insert(OddsSnapshot), at_tipoff)
        == "ck_odds_snapshots_captured_before_commence"
    )


def test_one_running_row_per_work_unit(conn: Connection) -> None:
    """Two running rows for the same job and scope are refused; finished rows may repeat."""
    unit = {"job_type": "schedule", "scope_key": "season=2026-27", "source": "nba_api"}
    done = unit | {"status": "succeeded", "finished_at": datetime.now(UTC)}
    running = unit | {"status": "running", "finished_at": None}
    conn.execute(insert(IngestionRun), [done, done, running])
    assert _violation(conn, insert(IngestionRun), running) == "uq_ingestion_runs_one_running"


def test_game_type_is_generated_and_views_pick_primary_rows(conn: Connection) -> None:
    """game_type comes from the game ID; the views return the preferred stat and closing line."""
    raw_id, event_id = _seed_event(conn)
    assert conn.execute(select(Game.game_type)).scalar_one() == "regular"

    line = {
        "game_id": "0022600001",
        "team_id": 1610612747,
        "opponent_team_id": 1610612744,
        "is_home": True,
        "minutes": 240,
        "plus_minus": 5,
        "fetched_at": T0,
        "raw_response_id": raw_id,
    } | dict.fromkeys(BOX_SCORE_COUNTS, 10)
    conn.execute(
        insert(TeamGameStat),
        [
            line | {"source": "boxscoretraditionalv3", "tov": 12},
            line | {"source": "leaguegamelog", "tov": 13},
        ],
    )
    primary = conn.execute(text("SELECT source, tov FROM team_game_stats_primary")).all()
    assert primary == [("leaguegamelog", 13)]

    for minutes in (10, 50):
        conn.execute(
            insert(OddsSnapshot), _snapshot(raw_id, event_id, T0 + timedelta(minutes=minutes))
        )
    closing = conn.execute(
        text("SELECT captured_at, lead_time, game_id FROM odds_closing_lines")
    ).all()
    assert closing == [(T0 + timedelta(minutes=50), timedelta(minutes=10), "0022600001")]


def test_sessions_use_utc(conn: Connection) -> None:
    """Connections from make_engine run with the UTC session time zone (schema v1 rule 1-2)."""
    assert conn.execute(text("SHOW timezone")).scalar_one() == "UTC"
