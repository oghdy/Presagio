"""odds_events / odds_snapshots loader, mapping and alias seed on a temp database (D-030)."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, text

from presagio.ingestion.client import IngestionClient, db_raw_store
from presagio.ingestion.errors import BlockedError, ClientRequestError, RetriesExhaustedError
from presagio.ingestion.loaders import (
    load_odds,
    load_odds_events,
    load_season_schedule,
    load_teams,
    remap_unmatched_events,
    seed_odds_team_aliases,
)
from presagio.ingestion.raw_store import store_response
from presagio.ingestion.sources.base import Provenance
from presagio.ingestion.sources.odds import (
    FixtureOddsSource,
    OddsBatch,
    OddsEventRecord,
    OddsOutcomeRecord,
    TheOddsApiSource,
)

from ..helpers import FakeClock, Handler
from ..odds_helpers import (
    E_DEN_LAC,
    E_PHI_CLE,
    E_STARTED,
    EVENTS_FIXTURE,
    FAKE_KEY,
    PHI_CLE_GAME,
    as_american,
    bump_prices,
    event_of,
    odds_handler,
    odds_json,
)
from .conftest import NbaApiStub

TIP_OFF = datetime(2026, 10, 22, 23, 0, tzinfo=UTC)


@dataclass
class OddsStub:
    """The Odds API adapter over a mutable fixture body, raw stored in the temp DB."""

    source: TheOddsApiSource
    bodies: dict[str, Any]
    handler: Handler
    clock: FakeClock


def make_odds(
    db: Engine, make_client: Callable[..., IngestionClient], clock: FakeClock, **kwargs: Any
) -> OddsStub:
    """Adapter -> IngestionClient -> MockTransport(odds fixtures) -> raw in db."""
    bodies = {"odds": odds_json(), "events": odds_json(EVENTS_FIXTURE)}
    handler = odds_handler(bodies)
    client = make_client(handler, store=db_raw_store(db))
    return OddsStub(TheOddsApiSource(client, FAKE_KEY, **kwargs), bodies, handler, clock)


@pytest.fixture
def teams_db(db: Engine, nba: NbaApiStub) -> Engine:
    """Teams loaded and the The Odds API alias seed applied; no games yet."""
    load_teams(nba.teams, db)
    with db.begin() as conn:
        seed_odds_team_aliases(conn)
    return db


@pytest.fixture
def odds_db(teams_db: Engine, nba: NbaApiStub) -> Engine:
    """Teams, aliases and the 2026-27 schedule (0022600006 PHI-CLE at 23:00Z) loaded."""
    load_season_schedule(nba.schedule, teams_db, "2026-27")
    return teams_db


@pytest.fixture
def odds(
    odds_db: Engine, make_client: Callable[..., IngestionClient], clock: FakeClock
) -> OddsStub:
    """The Odds API adapter wired to the fully loaded temp DB."""
    return make_odds(odds_db, make_client, clock)


def scalar(db: Engine, sql: str, **params: Any) -> Any:
    """One value from a query."""
    with db.connect() as conn:
        return conn.execute(text(sql), params).scalar_one()


def rows(db: Engine, sql: str, **params: Any) -> list[Any]:
    """All rows of a query as tuples."""
    with db.connect() as conn:
        return [tuple(r) for r in conn.execute(text(sql), params)]


def snapshot_count(db: Engine) -> int:
    """Rows in odds_snapshots."""
    return scalar(db, "SELECT count(*) FROM odds_snapshots")


def runs(db: Engine) -> list[Any]:
    """(job_type, scope_key, status) of every run in order."""
    return rows(db, "SELECT job_type, scope_key, status FROM ingestion_runs ORDER BY run_id")


def last_detail(db: Engine) -> dict[str, Any]:
    """error_detail of the latest run."""
    return scalar(db, "SELECT error_detail FROM ingestion_runs ORDER BY run_id DESC LIMIT 1")


def test_same_capture_twice_adds_no_rows(odds_db: Engine, odds: OddsStub) -> None:
    """Completion 4: the same response loaded again lands on the same raw row and keys."""
    first = load_odds(odds.source, odds_db, capture_label="prediction")
    assert (first.events.inserted, first.snapshots_inserted, first.snapshots_duplicate) == (
        3,
        18,
        0,
    )
    assert first.skipped == {"started_events": 1, "started_outcomes": 2, "rejected_outcomes": 0}
    captured = first.captured_at

    odds.clock.advance(3600)  # an hour later the provider still returns identical bytes
    second = load_odds(odds.source, odds_db, capture_label="prediction")
    assert second.captured_at == captured  # the raw row's first receipt, not this call
    assert (second.events.inserted, second.snapshots_inserted, second.snapshots_duplicate) == (
        0,
        0,
        18,
    )
    assert snapshot_count(odds_db) == 18
    assert scalar(odds_db, "SELECT count(*) FROM odds_events") == 3
    assert rows(
        odds_db, "SELECT fetch_count FROM raw_api_responses WHERE source='the_odds_api'"
    ) == [(2,)]
    assert runs(odds_db)[-2:] == [
        ("odds", "the_odds_api", "succeeded"),
        ("odds", "the_odds_api", "succeeded"),
    ]
    assert rows(odds_db, "SELECT DISTINCT captured_at, fetched_at FROM odds_snapshots") == [
        (captured, captured)
    ]


def test_three_captures_stay_apart_and_the_view_picks_the_last(
    odds_db: Engine, odds: OddsStub
) -> None:
    """Completion 2: three capture times -> three rows per price; closing = latest before tip."""
    start = odds.clock.now()
    times = []
    for hours, delta in ((0, 0.0), (2, 0.01), (5, 0.02)):
        odds.clock.advance((start + timedelta(hours=hours) - odds.clock.now()).total_seconds())
        odds.bodies["odds"] = bump_prices(odds_json(), delta)
        times.append(load_odds(odds.source, odds_db).captured_at)
    assert [t - times[0] for t in times] == [timedelta(0), timedelta(hours=2), timedelta(hours=5)]

    key = {"e": E_PHI_CLE}
    history = rows(
        odds_db,
        """SELECT s.captured_at, s.price_decimal FROM odds_snapshots s
           JOIN odds_events e USING (odds_event_id)
           WHERE e.source_event_id = :e AND s.bookmaker_key = 'draftkings'
             AND s.market_key = 'h2h' AND s.outcome_side = 'home' ORDER BY s.captured_at""",
        **key,
    )
    assert history == [
        (times[0], Decimal("1.8700")),
        (times[1], Decimal("1.8800")),
        (times[2], Decimal("1.8900")),
    ]
    assert snapshot_count(odds_db) == 3 * 18

    closing = rows(
        odds_db,
        """SELECT v.captured_at, v.price_decimal, v.game_id, v.lead_time, v.event_commence_time
           FROM odds_closing_lines v JOIN odds_events e USING (odds_event_id)
           WHERE e.source_event_id = :e AND v.bookmaker_key = 'draftkings'
             AND v.market_key = 'h2h' AND v.outcome_side = 'home'""",
        **key,
    )
    assert closing == [(times[2], Decimal("1.8900"), PHI_CLE_GAME, TIP_OFF - times[2], TIP_OFF)]
    # One closing row per (event, market, bookmaker, side): 12 for PHI-CLE, 6 for DEN-LAC.
    assert rows(
        odds_db,
        """SELECT e.source_event_id, count(*) FROM odds_closing_lines v
           JOIN odds_events e USING (odds_event_id) GROUP BY 1 ORDER BY 1""",
    ) == [(E_PHI_CLE, 12), (E_DEN_LAC, 6)]

    # Prediction-time odds (stage 2-4 rule): latest capture with captured_at <= predicted_at.
    predicted_at = times[0] + timedelta(hours=3)
    at_prediction = scalar(
        odds_db,
        """SELECT s.price_decimal FROM odds_snapshots s JOIN odds_events e USING (odds_event_id)
           WHERE e.source_event_id = :e AND s.bookmaker_key = 'draftkings'
             AND s.market_key = 'h2h' AND s.outcome_side = 'home' AND s.captured_at <= :p
           ORDER BY s.captured_at DESC LIMIT 1""",
        p=predicted_at,
        **key,
    )
    assert at_prediction == Decimal("1.8800")


def test_capture_after_tip_off_stores_no_prices(odds_db: Engine, odds: OddsStub) -> None:
    """Prices received at or after commence_time are skipped and counted, never inserted."""
    load_odds(odds.source, odds_db)
    odds.clock.advance((TIP_OFF + timedelta(minutes=30) - odds.clock.now()).total_seconds())
    odds.bodies["odds"] = bump_prices(odds_json(), 0.05)  # live prices after tip-off
    result = load_odds(odds.source, odds_db)
    assert result.snapshots_inserted == 6  # only DEN-LAC (2026-10-24) had not started
    assert result.skipped["started_events"] == 2
    assert result.skipped["started_outcomes"] == 2 + 12
    assert last_detail(odds_db)["skipped"]["started_events"] == 2
    assert (
        scalar(odds_db, "SELECT count(*) FROM odds_snapshots WHERE captured_at >= commence_time")
        == 0
    )
    # PHI-CLE closing line is still the pre-tip capture.
    assert (
        scalar(
            odds_db,
            """SELECT count(DISTINCT v.captured_at) FROM odds_closing_lines v
           JOIN odds_events e USING (odds_event_id) WHERE e.source_event_id = :e""",
            e=E_PHI_CLE,
        )
        == 1
    )


def test_mapping_fills_game_id_only_for_a_single_candidate(odds_db: Engine, odds: OddsStub) -> None:
    """A-9: PHI-CLE -> 0022600006; events with no game stay NULL with an issue."""
    result = load_odds(odds.source, odds_db)
    assert result.mapping.as_dict() == {
        "checked": 3,
        "matched": 1,
        "unmatched": 2,
        "max_time_diff_seconds": 0.0,
    }
    mapped = rows(
        odds_db,
        """SELECT source_event_id, game_id, match_method, matched_at IS NOT NULL
           FROM odds_events ORDER BY source_event_id""",
    )
    assert mapped == [
        (E_PHI_CLE, PHI_CLE_GAME, "alias+time", True),
        (E_STARTED, None, None, False),
        (E_DEN_LAC, None, None, False),
    ]
    issues = last_detail(odds_db)["quality_issues"]
    assert sorted((i["kind"], i["key"]) for i in issues) == [
        ("no_matching_game", E_STARTED),
        ("no_matching_game", E_DEN_LAC),
    ]
    assert runs(odds_db)[-1][2] == "succeeded"  # stored, just not matched yet (D-034 3)

    # Mapping happens once: a later capture does not touch the matched row.
    before = scalar(odds_db, "SELECT matched_at FROM odds_events WHERE game_id IS NOT NULL")
    odds.clock.advance(600)
    odds.bodies["odds"] = bump_prices(odds_json(), 0.01)
    assert load_odds(odds.source, odds_db).mapping.checked == 2
    assert scalar(odds_db, "SELECT matched_at FROM odds_events WHERE game_id IS NOT NULL") == before


def test_unmappable_events_stay_null(odds_db: Engine, odds: OddsStub) -> None:
    """Unknown alias, reversed home/away, and two candidate games all leave game_id NULL."""
    data = odds_json()
    den = event_of(data, E_DEN_LAC)
    den["away_team"] = "LA Clippers"
    for book in den["bookmakers"]:
        for market in book["markets"]:
            for o in market["outcomes"]:
                o["name"] = "LA Clippers" if o["name"] == "Los Angeles Clippers" else o["name"]
    phi = event_of(data, E_PHI_CLE)
    phi["home_team"], phi["away_team"] = phi["away_team"], phi["home_team"]
    odds.bodies["odds"] = data
    load_odds(odds.source, odds_db)
    issues = {i["key"]: i["kind"] for i in last_detail(odds_db)["quality_issues"]}
    assert issues == {
        E_DEN_LAC: "unknown_team_alias",
        E_PHI_CLE: "home_away_swapped",
        E_STARTED: "no_matching_game",
    }
    assert scalar(odds_db, "SELECT count(*) FROM odds_events WHERE game_id IS NOT NULL") == 0


def test_two_candidate_games_are_ambiguous(odds_db: Engine, odds: OddsStub) -> None:
    """A second PHI-CLE game 3 h earlier makes the match ambiguous: NULL, not a guess."""
    with odds_db.begin() as conn:
        conn.execute(
            text(
                """INSERT INTO games (game_id, season, game_subtype, start_time_utc, is_time_tbd,
                     status_code, status_text, postponed_status, home_team_id, away_team_id,
                     home_score, away_score, is_neutral, if_necessary, source, fetched_at,
                     raw_response_id)
                   SELECT '0022600007', season, game_subtype, start_time_utc - interval '3 hours',
                     is_time_tbd, status_code, status_text, postponed_status, home_team_id,
                     away_team_id, home_score, away_score, is_neutral, if_necessary, source,
                     fetched_at, raw_response_id
                   FROM games WHERE game_id = :g"""
            ),
            {"g": PHI_CLE_GAME},
        )
    load_odds(odds.source, odds_db)
    issue = next(i for i in last_detail(odds_db)["quality_issues"] if i["key"] == E_PHI_CLE)
    assert issue["kind"] == "ambiguous_game"
    assert "0022600006" in issue["message"] and "0022600007" in issue["message"]
    assert scalar(odds_db, "SELECT count(*) FROM odds_events WHERE game_id IS NOT NULL") == 0


def test_remap_fills_events_once_games_arrive(
    teams_db: Engine,
    nba: NbaApiStub,
    make_client: Callable[..., IngestionClient],
    clock: FakeClock,
) -> None:
    """Odds before the schedule: NULL first, then remap_unmatched_events fills PHI-CLE."""
    odds = make_odds(teams_db, make_client, clock)
    assert load_odds(odds.source, teams_db).mapping.matched == 0
    load_season_schedule(nba.schedule, teams_db, "2026-27")
    remapped = remap_unmatched_events(teams_db)
    assert (remapped.checked, remapped.matched) == (3, 1)
    assert (
        scalar(teams_db, "SELECT game_id FROM odds_events WHERE source_event_id = :e", e=E_PHI_CLE)
        == PHI_CLE_GAME
    )
    assert remap_unmatched_events(teams_db).checked == 2  # matched rows are never revisited


def test_american_odds_are_stored_as_decimal(
    odds_db: Engine, make_client: Callable[..., IngestionClient], clock: FakeClock
) -> None:
    """Completion 3: oddsFormat=american prices land in price_decimal (-110 -> 1.9091)."""
    stub = make_odds(odds_db, make_client, clock, odds_format="american")
    stub.bodies["odds"] = as_american(odds_json())
    load_odds(stub.source, odds_db)
    (request,) = stub.handler.seen  # type: ignore[attr-defined]
    assert request.url.params["oddsFormat"] == "american"
    prices = rows(
        odds_db,
        """SELECT s.market_key, s.outcome_side, s.price_decimal, s.point FROM odds_snapshots s
           JOIN odds_events e USING (odds_event_id)
           WHERE e.source_event_id = :e ORDER BY 1, 2""",
        e=E_DEN_LAC,
    )
    assert prices == [
        ("h2h", "away", Decimal("2.3500"), None),
        ("h2h", "home", Decimal("1.6211"), None),
        ("spreads", "away", Decimal("1.9091"), Decimal("4.50")),
        ("spreads", "home", Decimal("1.9091"), Decimal("-4.50")),
        ("totals", "over", Decimal("1.8696"), Decimal("229.50")),
        ("totals", "under", Decimal("1.9524"), Decimal("229.50")),
    ]


def test_events_only_load_maps_without_prices(odds_db: Engine, odds: OddsStub) -> None:
    """/events (free): events stored and mapped, no snapshots, its own job_type."""
    result = load_odds_events(odds.source, odds_db)
    assert (result.events.inserted, result.snapshots_inserted, result.mapping.matched) == (3, 0, 1)
    assert snapshot_count(odds_db) == 0
    assert runs(odds_db)[-1] == ("odds_events", "the_odds_api", "succeeded")
    # The later /odds capture reuses the same event rows.
    assert load_odds(odds.source, odds_db).events.inserted == 0


def test_unreadable_event_or_price_makes_the_run_partial(odds_db: Engine, odds: OddsStub) -> None:
    """A rejected event is a failed item; a dropped price marks the run partial (D-034 3)."""
    data = odds_json()
    del event_of(data, E_DEN_LAC)["home_team"]
    event_of(data, E_PHI_CLE)["bookmakers"][0]["markets"][0]["outcomes"][0]["price"] = 1.0
    odds.bodies["odds"] = data
    result = load_odds(odds.source, odds_db)
    assert list(result.failed) == [E_DEN_LAC]
    assert result.snapshots_inserted == 11
    run = rows(
        odds_db,
        "SELECT status, items_total, items_succeeded, items_failed FROM ingestion_runs"
        " ORDER BY run_id DESC LIMIT 1",
    )
    assert run == [("partial", 3, 2, 1)]
    kinds = [i["kind"] for i in last_detail(odds_db)["quality_issues"]]
    assert "bad_price" in kinds


def test_blocked_429_aborts_and_keeps_the_raw_body(
    odds_db: Engine, make_client: Callable[..., IngestionClient], clock: FakeClock
) -> None:
    """D-032: quota exhausted shows up as 429 -> run aborted, body in raw, nothing stored."""
    handler = odds_handler({"odds": {"message": "Usage quota has been reached"}}, status=429)
    source = TheOddsApiSource(make_client(handler, store=db_raw_store(odds_db)), FAKE_KEY)
    with pytest.raises(BlockedError):
        load_odds(source, odds_db)
    assert runs(odds_db)[-1] == ("odds", "the_odds_api", "aborted")
    assert rows(
        odds_db, "SELECT http_status FROM raw_api_responses WHERE source = 'the_odds_api'"
    ) == [(429,)]
    assert snapshot_count(odds_db) == 0


def test_fixture_source_records_load_the_same_way(odds_db: Engine) -> None:
    """Completion 1: records from any OddsSource (here hand-made) go through the same loader."""
    fetched = datetime(2026, 10, 22, 15, 0, tzinfo=UTC)
    stored = store_response(
        odds_db,
        source="the_odds_api",
        endpoint="fixture",
        params={},
        http_status=200,
        body=b"[]",
        fetched_at=fetched,
    )
    outcome = OddsOutcomeRecord(
        "pinnacle", fetched, "totals", "Over", "over", Decimal("1.9500"), Decimal("220.5")
    )
    batch = OddsBatch(
        source="the_odds_api",
        captured_at=fetched,
        capture_kind="live",
        events=(
            OddsEventRecord(
                "hand-made-1", "Philadelphia 76ers", "Cleveland Cavaliers", TIP_OFF, (outcome,)
            ),
        ),
        provenance=Provenance("fixture", fetched, stored.raw_response_id),
    )
    result = load_odds(FixtureOddsSource([batch]), odds_db, capture_label="pre_tip")
    assert (result.snapshots_inserted, result.mapping.matched) == (1, 1)
    assert rows(odds_db, "SELECT bookmaker_key, capture_label, point FROM odds_snapshots") == [
        ("pinnacle", "pre_tip", Decimal("220.50"))
    ]
    assert runs(odds_db)[-1] == ("odds", "fixture", "succeeded")


def test_seed_aliases_is_idempotent(db: Engine, nba: NbaApiStub) -> None:
    """30 The Odds API names; applying the seed twice keeps 30 rows. Teams must exist first."""
    with db.begin() as conn, pytest.raises(ValueError, match="load teams first"):
        seed_odds_team_aliases(conn)
    load_teams(nba.teams, db)
    with db.begin() as conn:
        first = seed_odds_team_aliases(conn)
    with db.begin() as conn:
        second = seed_odds_team_aliases(conn)
    assert (first.inserted, second.inserted, second.updated) == (30, 0, 30)
    count = "SELECT count(*) FROM team_aliases WHERE source = 'the_odds_api'"
    assert scalar(db, count) == 30
    assert (
        scalar(
            db,
            """SELECT t.tricode FROM team_aliases a JOIN teams t USING (team_id)
           WHERE a.source = 'the_odds_api' AND a.alias = 'Los Angeles Clippers'""",
        )
        == "LAC"
    )
    assert (
        scalar(db, "SELECT count(DISTINCT team_id) FROM team_aliases WHERE source = 'the_odds_api'")
        == 30
    )


def test_key_is_never_stored_logged_or_raised(
    odds_db: Engine,
    make_client: Callable[..., IngestionClient],
    clock: FakeClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The fake key is sent but appears in no table, log line, exception or repr (D-031)."""
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="presagio")
    seen: list[str] = []
    messages: list[str] = []
    stub = make_odds(odds_db, make_client, clock)
    load_odds(stub.source, odds_db)
    load_odds_events(stub.source, odds_db)
    seen += [str(r.url) for r in stub.handler.seen]  # type: ignore[attr-defined]
    for status, error in (
        (401, ClientRequestError),
        (429, BlockedError),
        (500, RetriesExhaustedError),
    ):
        handler = odds_handler({"odds": {"message": f"status {status}"}}, status=status)
        source = TheOddsApiSource(make_client(handler, store=db_raw_store(odds_db)), FAKE_KEY)
        with pytest.raises(error) as excinfo:
            load_odds(source, odds_db)
        messages += [str(excinfo.value), repr(excinfo.value), excinfo.exconly(), repr(source)]
        seen += [str(r.url) for r in handler.seen]  # type: ignore[attr-defined]

    assert seen and all(f"apiKey={FAKE_KEY}" in url for url in seen)  # it really was sent
    dump = []
    with odds_db.connect() as conn:
        for table in (
            "raw_api_responses",
            "ingestion_runs",
            "odds_events",
            "odds_snapshots",
            "team_aliases",
        ):
            dump += (
                conn.execute(text(f"SELECT row_to_json(t)::text FROM {table} t")).scalars().all()
            )
    assert len(dump) > 30
    haystack = "\n".join([*dump, *messages, caplog.text])
    assert FAKE_KEY not in haystack
    assert FAKE_KEY.lower() not in haystack.lower()
