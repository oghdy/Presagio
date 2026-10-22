"""The Odds API adapter: response parsing, request shape, key handling (no network)."""

import re
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from pydantic import SecretStr

from presagio.config import Settings
from presagio.ingestion.client import IngestionClient
from presagio.ingestion.sources.base import Provenance, SourceFormatError
from presagio.ingestion.sources.odds import (
    FixtureOddsSource,
    OddsSource,
    TheOddsApiSource,
    parse_odds,
)
from presagio.ingestion.sources.odds.base import OddsOutcomeRecord
from presagio.ingestion.sources.odds.the_odds_api import TEAM_NAME_SEED

from ..helpers import T0, FakeStore
from ..odds_helpers import (
    E_DEN_LAC,
    E_PHI_CLE,
    E_STARTED,
    EVENTS_FIXTURE,
    FAKE_KEY,
    ODDS_FIXTURES,
    QUOTA,
    REAL_EVENTS_FIXTURE,
    REAL_ODDS_FIXTURE,
    REAL_PHI_CLE,
    as_american,
    event_of,
    odds_handler,
    odds_json,
)

PROV = Provenance("odds", T0, 7)


def parse(data: Any, **kwargs: Any) -> Any:
    """parse_odds captured at T0 with raw row 7."""
    return parse_odds(data, provenance=PROV, **kwargs)


def by_key(event: Any) -> dict[tuple[str, str, str], OddsOutcomeRecord]:
    """An event's outcomes keyed by (bookmaker, market, side)."""
    return {(o.bookmaker_key, o.market_key, o.outcome_side): o for o in event.outcomes}


def test_parse_fixture_into_long_format() -> None:
    """One record per (bookmaker, market, side); started events keep no prices."""
    batch = parse(odds_json())
    assert batch.source == "the_odds_api"
    assert (batch.captured_at, batch.capture_kind) == (T0, "live")
    assert [e.source_event_id for e in batch.events] == [E_STARTED, E_PHI_CLE, E_DEN_LAC]
    assert batch.outcome_count == 12 + 6
    assert dict(batch.skipped) == {
        "started_events": 1,
        "started_outcomes": 2,
        "rejected_outcomes": 0,
    }
    assert batch.issues == () and dict(batch.rejected) == {}

    phi = batch.events[1]
    assert (phi.home_team_name, phi.away_team_name) == ("Philadelphia 76ers", "Cleveland Cavaliers")
    assert phi.commence_time == datetime(2026, 10, 22, 23, 0, tzinfo=UTC)
    outcomes = by_key(phi)
    h2h = outcomes[("draftkings", "h2h", "home")]
    assert (h2h.outcome_name, h2h.price_decimal, h2h.point) == (
        "Philadelphia 76ers",
        Decimal("1.8700"),
        None,
    )
    assert h2h.bookmaker_last_update == datetime(2026, 10, 22, 11, 58, 10, tzinfo=UTC)
    spread = outcomes[("fanduel", "spreads", "away")]
    assert (spread.price_decimal, spread.point) == (Decimal("1.9000"), Decimal("1.5"))
    total = outcomes[("fanduel", "totals", "under")]
    assert (total.outcome_name, total.price_decimal, total.point) == (
        "Under",
        Decimal("1.9200"),
        Decimal("222.0"),
    )


def test_american_prices_are_stored_as_decimal() -> None:
    """oddsFormat=american: -110 -> 1.9091, +135 -> 2.35; same records as decimal input."""
    american = as_american(odds_json())
    den = event_of(american, E_DEN_LAC)["bookmakers"][0]["markets"]
    assert [o["price"] for o in den[0]["outcomes"]] == [-161, 135]
    assert [o["price"] for o in den[1]["outcomes"]] == [-110, -110]

    batch = parse(american, odds_format="american")
    outcomes = by_key(batch.events[2])
    assert outcomes[("draftkings", "h2h", "away")].price_decimal == Decimal("2.3500")
    assert outcomes[("draftkings", "spreads", "home")].price_decimal == Decimal("1.9091")
    # 1.62 -> -161.29 -> -161 (API sends integers) -> 1.6211
    assert outcomes[("draftkings", "h2h", "home")].price_decimal == Decimal("1.6211")


def test_unusable_prices_are_left_out_with_issues() -> None:
    """Unknown team name, missing point, h2h point, duplicate side, price 1.0, extra market."""
    data = odds_json()
    markets = event_of(data, E_DEN_LAC)["bookmakers"][0]["markets"]
    markets[0]["outcomes"][1]["name"] = "LA Clippers"  # not the event's away_team
    del markets[1]["outcomes"][0]["point"]
    markets[0]["outcomes"][0]["point"] = 0.5
    markets[2]["outcomes"].append({"name": "Over", "price": 1.8, "point": 230.5})
    markets[2]["outcomes"][1]["price"] = 1.0
    markets.append({"key": "h2h_q1", "outcomes": [{"name": "Denver Nuggets", "price": 1.7}]})

    batch = parse(data)
    kinds = sorted(i.kind for i in batch.issues)
    assert kinds == [
        "bad_price",
        "duplicate_outcome",
        "missing_point",
        "unexpected_point",
        "unknown_outcome_name",
        "unrequested_market",
    ]
    assert all(i.key == E_DEN_LAC for i in batch.issues)
    assert batch.skipped["rejected_outcomes"] == 5
    # DEN-LAC kept only spreads/away and totals/over (the first Over, not the duplicate).
    kept = by_key(batch.events[2])
    assert sorted(kept) == [("draftkings", "spreads", "away"), ("draftkings", "totals", "over")]
    assert kept[("draftkings", "totals", "over")].point == Decimal("229.5")


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (lambda e: e.pop("home_team"), "missing field 'home_team'"),
        (lambda e: e.update(commence_time="10/22/2026 23:00"), "not an ISO time"),
        (lambda e: e.update(commence_time="2026-10-22T23:00:00"), "no UTC offset"),
        (lambda e: e.update(away_team=e["home_team"]), "bad teams"),
        (lambda e: e["bookmakers"][0].pop("last_update"), "missing field 'last_update'"),
    ],
)
def test_malformed_event_is_rejected_alone(change: Callable[[Any], Any], reason: str) -> None:
    """One broken event goes to rejected with its reason; the other events are kept."""
    data = odds_json()
    change(event_of(data, E_PHI_CLE))
    batch = parse(data)
    assert [e.source_event_id for e in batch.events] == [E_STARTED, E_DEN_LAC]
    assert list(batch.rejected) == [E_PHI_CLE]
    assert reason in batch.rejected[E_PHI_CLE]


def test_other_sport_and_bad_top_level() -> None:
    """A non-NBA event is skipped with an issue; a non-list body is a format error."""
    data = odds_json()
    event_of(data, E_DEN_LAC)["sport_key"] = "basketball_wnba"
    batch = parse(data)
    assert [i.kind for i in batch.issues] == ["other_sport"]
    assert len(batch.events) == 2
    with pytest.raises(SourceFormatError, match="expected a JSON list"):
        parse({"message": "nope"})


def test_events_response_has_no_prices() -> None:
    """/events: same events, no bookmakers, nothing skipped."""
    batch = parse(odds_json(EVENTS_FIXTURE))
    assert [e.source_event_id for e in batch.events] == [E_STARTED, E_PHI_CLE, E_DEN_LAC]
    assert batch.outcome_count == 0
    assert batch.skipped["started_events"] == 0


def test_records_refuse_what_the_database_would_refuse() -> None:
    """OddsOutcomeRecord checks market/side/point/price before any SQL."""
    good: dict[str, Any] = {
        "bookmaker_key": "draftkings",
        "bookmaker_last_update": T0,
        "market_key": "totals",
        "outcome_name": "Over",
        "outcome_side": "over",
        "price_decimal": Decimal("1.9100"),
        "point": Decimal("221.5"),
    }
    OddsOutcomeRecord(**good)
    for change in (
        {"outcome_side": "home"},
        {"market_key": "h2h"},
        {"point": None},
        {"price_decimal": Decimal("1.0000")},
        {"price_decimal": Decimal("1.91234")},
        {"market_key": "outrights"},
        {"bookmaker_last_update": datetime(2026, 10, 22, 12)},
    ):
        with pytest.raises(ValueError):
            OddsOutcomeRecord(**{**good, **change})


def test_request_shape_and_key_only_in_secret_params(
    make_client: Callable[..., IngestionClient],
) -> None:
    """GET .../v4/sports/basketball_nba/odds?regions=us&markets=...; apiKey sent, not stored."""
    handler = odds_handler({"odds": odds_json()})
    store = FakeStore()
    source = TheOddsApiSource(make_client(handler, store=store), FAKE_KEY)
    batch = source.fetch_odds(capture_label="prediction")

    (request,) = handler.seen  # type: ignore[attr-defined]
    assert request.url.host == "api.the-odds-api.com"
    assert request.url.path == "/v4/sports/basketball_nba/odds"
    assert dict(request.url.params) == {
        "apiKey": FAKE_KEY,
        "markets": "h2h,spreads,totals",
        "oddsFormat": "decimal",
        "regions": "us",
    }
    (stored,) = store.calls
    assert stored["params"] == {
        "regions": "us",
        "markets": "h2h,spreads,totals",
        "oddsFormat": "decimal",
    }
    assert (stored["source"], stored["endpoint"]) == ("the_odds_api", "odds")
    assert batch.capture_label == "prediction"
    assert dict(batch.quota) == QUOTA
    assert batch.provenance.fetched_at == batch.captured_at == T0
    assert FAKE_KEY not in repr(source) and FAKE_KEY not in repr(source.odds_request())


def test_events_request_is_the_free_endpoint(make_client: Callable[..., IngestionClient]) -> None:
    """fetch_events calls /events with no parameters except the key."""
    handler = odds_handler({"events": odds_json(EVENTS_FIXTURE)})
    source = TheOddsApiSource(make_client(handler), FAKE_KEY)
    batch = source.fetch_events()
    (request,) = handler.seen  # type: ignore[attr-defined]
    assert request.url.path == "/v4/sports/basketball_nba/events"
    assert dict(request.url.params) == {"apiKey": FAKE_KEY}
    assert len(batch.events) == 3 and batch.outcome_count == 0


def test_empty_key_disables_the_adapter(make_client: Callable[..., IngestionClient]) -> None:
    """ODDS_API_KEY empty/unset -> from_settings gives None; the constructor refuses ''."""
    client = make_client(odds_handler({}))
    base = {"_env_file": None, "database_url": "postgresql+psycopg://u:p@127.0.0.1:1/x"}
    for key in (None, "", "   "):
        settings = Settings(**base, odds_api_key=key)  # type: ignore[arg-type]
        assert settings.odds_api_key is None
        assert TheOddsApiSource.from_settings(client, settings) is None
    with pytest.raises(ValueError, match="disabled"):
        TheOddsApiSource(client, "")

    configured = {**base, "odds_api_key": FAKE_KEY}
    settings = Settings(**configured, odds_api_markets="h2h")  # type: ignore[arg-type]
    source = TheOddsApiSource.from_settings(client, settings)
    assert source is not None and source.markets == "h2h"
    assert isinstance(settings.odds_api_key, SecretStr)
    assert FAKE_KEY not in repr(settings) and FAKE_KEY not in str(settings)


def test_both_sources_satisfy_the_protocol(make_client: Callable[..., IngestionClient]) -> None:
    """The loader only needs OddsSource; the real adapter and the fixture source both are."""
    assert isinstance(TheOddsApiSource(make_client(odds_handler({})), FAKE_KEY), OddsSource)
    assert isinstance(FixtureOddsSource([]), OddsSource)


def test_fixture_source_returns_batches_in_order() -> None:
    """Batches come back one per call; fetch_events strips prices; exhausted -> LookupError."""
    first, second = parse(odds_json()), parse(odds_json())
    source = FixtureOddsSource([first, second])
    assert source.fetch_odds(capture_label="pre_tip").capture_label == "pre_tip"
    assert source.fetch_events().outcome_count == 0
    with pytest.raises(LookupError):
        source.fetch_odds()


def test_odds_fixtures_carry_no_key() -> None:
    """Saved responses must not hold a key; gitleaks misses '?apiKey=<32 hex>' in a URL."""
    files = sorted(ODDS_FIXTURES.iterdir())
    assert files
    for path in files:
        content = path.read_text()
        assert "apikey" not in content.lower(), path.name
        # Event ids are 32 hex too, so only flag hex strings outside an "id" field.
        for match in re.finditer(r'"(\w+)":\s*"[0-9a-f]{32}"', content):
            assert match.group(1) == "id", f"{path.name}: {match.group(0)}"


def test_real_response_parses_without_issues() -> None:
    """The 2026-10-22 /odds response: 3 events, 3+3+1 bookmakers x 3 markets x 2 sides."""
    batch = parse(odds_json(REAL_ODDS_FIXTURE))
    assert [len(e.outcomes) for e in batch.events] == [18, 18, 6]
    assert batch.issues == () and dict(batch.rejected) == {}
    assert dict(batch.skipped) == {
        "started_events": 0,
        "started_outcomes": 0,
        "rejected_outcomes": 0,
    }
    phi = batch.events[0]
    assert (phi.source_event_id, phi.home_team_name, phi.away_team_name) == (
        REAL_PHI_CLE,
        "Philadelphia 76ers",
        "Cleveland Cavaliers",
    )
    assert phi.commence_time == datetime(2026, 10, 22, 23, 10, tzinfo=UTC)
    outcomes = by_key(phi)
    # The real h2h lists the away team first; sides come from names, not positions.
    assert outcomes[("draftkings", "h2h", "away")].price_decimal == Decimal("2.2400")
    assert outcomes[("draftkings", "h2h", "home")].price_decimal == Decimal("1.6800")
    assert outcomes[("draftkings", "spreads", "home")].point == Decimal("-3.5")
    assert outcomes[("draftkings", "totals", "over")].point == Decimal("235.5")
    assert outcomes[("draftkings", "h2h", "home")].bookmaker_last_update == datetime(
        2026, 10, 22, 12, 38, 11, tzinfo=UTC
    )
    assert {o.bookmaker_key for o in phi.outcomes} == {"draftkings", "bovada", "betmgm"}


def test_real_team_names_are_the_seed() -> None:
    """Every team name in the real /events response is a seeded the_odds_api alias."""
    events = odds_json(REAL_EVENTS_FIXTURE)
    names = {e["home_team"] for e in events} | {e["away_team"] for e in events}
    assert names <= set(TEAM_NAME_SEED.values())
    assert len(parse(events).events) == 3
