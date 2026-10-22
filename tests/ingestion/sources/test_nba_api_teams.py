"""nba_api team adapter: static list + CommonTeamYears check (no DB, no network)."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from nba_api.stats.static import teams as static_teams

from presagio.ingestion.client import IngestionClient
from presagio.ingestion.sources import TeamSource
from presagio.ingestion.sources.base import Provenance
from presagio.ingestion.sources.nba_api import NbaApiTeamSource, build_team_batch

from ..helpers import T0, FakeRun, fixture_json, nba_fixture_handler

PROV = Provenance("nba_api.static", datetime(2026, 10, 22, tzinfo=UTC), 1)


def test_thirty_teams_and_two_aliases_each_with_no_issues() -> None:
    """The library's 30 teams match the 30 active CommonTeamYears franchises."""
    batch = build_team_batch(static_teams.get_teams(), fixture_json("commonteamyears__00"), PROV)
    assert len(batch.teams) == 30
    assert len({t.tricode for t in batch.teams}) == 30
    assert batch.issues == ()
    lac = next(t for t in batch.teams if t.tricode == "LAC")
    assert (lac.team_id, lac.city, lac.nickname, lac.full_name) == (
        1610612746,
        "Los Angeles",
        "Clippers",
        "Los Angeles Clippers",
    )
    aliases = {(a.source, a.alias): a.team_id for a in batch.aliases}
    assert len(aliases) == 60
    assert aliases[("nba_api", "Los Angeles Clippers")] == 1610612746
    assert aliases[("nba_api", "LAC")] == 1610612746


def test_disagreements_with_commonteamyears_are_issues() -> None:
    """A franchise missing from the library, a stale tricode, an inactive team: all reported."""
    static: list[dict[str, Any]] = [dict(t) for t in static_teams.get_teams()]
    removed = static.pop()
    static[0]["abbreviation"] = "XXX"
    years = fixture_json("commonteamyears__00")
    rows = years["resultSets"][0]["rowSet"]
    # Pretend the second library team stopped playing after 2020.
    second = next(row for row in rows if row[1] == static[1]["id"])
    second[3] = "2020"

    batch = build_team_batch(static, years, PROV)
    assert sorted((i.kind, i.key) for i in batch.issues) == sorted(
        [
            ("team_not_in_static_list", str(removed["id"])),
            ("tricode_mismatch", str(static[0]["id"])),
            ("team_not_active", str(static[1]["id"])),
        ]
    )


def test_source_fetches_commonteamyears_once(
    make_client: Callable[..., IngestionClient],
) -> None:
    """One call; fetched_at is when the check response arrived (fake clock)."""
    handler = nba_fixture_handler()
    source = NbaApiTeamSource(make_client(handler))
    assert isinstance(source, TeamSource)
    run = FakeRun()
    batch = source.fetch_teams(run)
    (request,) = handler.seen  # type: ignore[attr-defined]
    assert request.url.path == "/stats/commonteamyears"
    assert run.api_calls == 1
    assert batch.provenance.source == "nba_api.static"
    assert batch.provenance.fetched_at == T0
    assert len(batch.teams) == 30
