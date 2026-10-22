"""nba_api schedule/result adapter: fixture responses -> neutral records (no DB, no network)."""

import copy
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from nba_api.library.http import NBAHTTP

from presagio.ingestion.client import IngestionClient
from presagio.ingestion.sources import (
    ResultSource,
    ScheduleSource,
    SourceFormatError,
    game_type_of,
    season_of,
)
from presagio.ingestion.sources.base import Provenance
from presagio.ingestion.sources.nba_api import (
    NbaApiScheduleSource,
    parse_game_log,
    parse_schedule,
)

from ..helpers import FakeRun, fixture_json, nba_fixture_handler

PROV = Provenance("scheduleleaguev2", datetime(2026, 10, 22, 12, tzinfo=UTC), 1)
LOG_PROV = Provenance("leaguegamelog", datetime(2026, 10, 22, 12, tzinfo=UTC), 2)


def schedule_games(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Every game object in a ScheduleLeagueV2 response."""
    return [g for day in data["leagueSchedule"]["gameDates"] for g in day["games"]]


@pytest.fixture(autouse=True)
def no_direct_nba_api_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """D-031: nba_api must never send a request itself; only IngestionClient does."""

    def refuse(*args: Any, **kwargs: Any) -> Any:
        """Fail loudly if nba_api tries to send."""
        raise AssertionError("nba_api sent a request itself (get_request must be False)")

    monkeypatch.setattr(NBAHTTP, "send_api_request", refuse)


def test_game_id_helpers() -> None:
    """Type from the third digit, season from digits 4-5 (D-025, schema 6-1)."""
    assert game_type_of("0022300061") == "regular"
    assert game_type_of("0042300111") == "playoffs"
    assert game_type_of("0052500111") == "playin"
    assert game_type_of("0062600001") == "cup_final"
    assert game_type_of("0012300001") == "preseason"
    assert season_of("0022300061") == "2023-24"
    assert season_of("0029900001") == "2099-00"
    with pytest.raises(ValueError):
        season_of("22300061")


@pytest.mark.parametrize("season", ["2023-24", "2024-25", "2025-26"])
def test_finished_seasons_keep_stored_types_and_count_preseason(season: str) -> None:
    """Each finished-season fixture has one game per type; preseason is skipped and counted."""
    batch = parse_schedule(fixture_json(f"scheduleleaguev2__{season}"), season, PROV)
    types = sorted(game_type_of(g.game_id) for g in batch.games)
    assert types == ["cup_final", "playin", "playoffs", "regular"]
    assert batch.skipped == {"preseason": 1}
    assert batch.issues == ()
    assert all(g.season == season for g in batch.games)
    assert all(g.status_code == 3 and g.home_score is not None for g in batch.games)


def test_start_time_is_utc_from_gamedatetimeutc_not_est() -> None:
    """0022300061: gameDateTimeUTC 23:30Z; the fake-Z gameDateTimeEst says 19:30Z."""
    batch = parse_schedule(fixture_json("scheduleleaguev2__2023-24"), "2023-24", PROV)
    game = next(g for g in batch.games if g.game_id == "0022300061")
    assert game.start_time_utc == datetime(2023, 10, 24, 23, 30, tzinfo=UTC)
    assert game.start_time_utc.utcoffset().total_seconds() == 0  # type: ignore[union-attr]
    assert (game.home_team_id, game.away_team_id) == (1610612743, 1610612747)  # DEN vs LAL
    assert (game.home_score, game.away_score) == (119, 107)
    assert game.is_time_tbd is False
    assert game.postponed_status == "A"


def test_current_season_scheduled_tbd_and_final_games() -> None:
    """2026-27: two finals, one scheduled, one TBD with no teams, a cup final with no teams."""
    batch = parse_schedule(fixture_json("scheduleleaguev2__2026-27"), "2026-27", PROV)
    games = {g.game_id: g for g in batch.games}
    assert set(games) == {"0022600001", "0022600002", "0022600006", "0022601201", "0062600001"}

    final = games["0022600001"]
    assert (final.status_code, final.status_text) == (3, "Final")
    assert (final.home_score, final.away_score) == (121, 113)

    scheduled = games["0022600006"]
    assert (scheduled.status_code, scheduled.status_text) == (1, "7:00 pm ET")
    # The source sends score 0 for unplayed games; we keep "no score yet" as None.
    assert (scheduled.home_score, scheduled.away_score) == (None, None)
    assert scheduled.start_time_utc == datetime(2026, 10, 22, 23, 0, tzinfo=UTC)

    tbd = games["0022601201"]
    assert tbd.is_time_tbd is True
    assert (tbd.home_team_id, tbd.away_team_id) == (None, None)
    assert tbd.game_subtype == "in-season-knockout"

    cup_final = games["0062600001"]
    assert cup_final.is_time_tbd is False  # time is set, teams are not (A-5)
    assert (cup_final.home_team_id, cup_final.away_team_id) == (None, None)
    assert cup_final.is_neutral is True
    assert cup_final.if_necessary is False  # the schedule sends the string "false"
    assert games["0022600002"].game_subtype is None  # "" -> None


def test_season_mismatch_is_rejected() -> None:
    """A response for another season than requested is a format error, not data."""
    with pytest.raises(SourceFormatError, match="asked for 2025-26"):
        parse_schedule(fixture_json("scheduleleaguev2__2026-27"), "2025-26", PROV)


def test_bad_games_become_issues_not_exceptions() -> None:
    """Duplicate IDs, malformed IDs and a final game without teams are reported and skipped."""
    data = fixture_json("scheduleleaguev2__2026-27")
    day = data["leagueSchedule"]["gameDates"][0]["games"]
    duplicate = copy.deepcopy(day[0])
    duplicate["gameStatusText"] = "Final/OT"
    broken = copy.deepcopy(day[0])
    broken["gameId"] = "22600001"
    teamless_final = copy.deepcopy(day[1])
    teamless_final["gameId"] = "0022600099"
    teamless_final["homeTeam"]["teamId"] = 0
    day.extend([duplicate, broken, teamless_final])

    batch = parse_schedule(data, "2026-27", PROV)
    kinds = sorted((i.kind, i.key) for i in batch.issues)
    assert kinds == [
        ("duplicate_game", "0022600001"),
        ("invalid_game_id", "22600001"),
        ("unusable_game", "0022600099"),
    ]
    games = {g.game_id: g for g in batch.games}
    assert games["0022600001"].status_text == "Final/OT"  # last listing wins
    assert "0022600099" not in games


def test_missing_field_is_a_format_error() -> None:
    """No silent defaults: a game without gameDateTimeUTC is reported, not invented."""
    data = fixture_json("scheduleleaguev2__2026-27")
    del schedule_games(data)[0]["gameDateTimeUTC"]
    batch = parse_schedule(data, "2026-27", PROV)
    assert [(i.kind, i.key) for i in batch.issues] == [("unusable_game", "0022600001")]
    assert "gameDateTimeUTC" in batch.issues[0].message


def test_game_log_pairs_rows_by_matchup() -> None:
    """'DEN vs. LAL' is the home row, 'LAL @ DEN' the away row."""
    batch = parse_game_log(
        fixture_json("leaguegamelog__2023-24_RegularSeason"), "2023-24", "regular", LOG_PROV
    )
    results = {r.game_id: r for r in batch.results}
    assert set(results) == {"0022300061", "0022300062"}
    den_lal = results["0022300061"]
    assert (den_lal.home_team_id, den_lal.away_team_id) == (1610612743, 1610612747)
    assert (den_lal.home_score, den_lal.away_score) == (119, 107)
    assert batch.issues == ()


@pytest.mark.parametrize(
    ("name", "season", "season_type"),
    [
        ("leaguegamelog__2023-24_Playoffs", "2023-24", "playoffs"),
        ("leaguegamelog__2025-26_PlayIn", "2025-26", "playin"),
    ],
)
def test_game_log_other_season_types(name: str, season: str, season_type: str) -> None:
    """Playoff and play-in logs parse to two games each with no issues."""
    batch = parse_game_log(fixture_json(name), season, season_type, LOG_PROV)
    assert len(batch.results) == 2
    assert batch.issues == ()


def test_game_log_problems_are_issues() -> None:
    """Two home rows, a game of the wrong type, and a row without a result are reported."""
    data = fixture_json("leaguegamelog__2023-24_RegularSeason")
    headers = data["resultSets"][0]["headers"]
    rows = data["resultSets"][0]["rowSet"]
    matchup, wl, game = (headers.index(n) for n in ("MATCHUP", "WL", "GAME_ID"))
    for row in rows:
        if row[game] == "0022300061":
            row[matchup] = row[matchup].replace(" @ ", " vs. ")  # both rows claim home
        else:
            row[wl] = None  # not final yet
    extra = list(rows[0])
    extra[game] = "0042300111"
    rows.append(extra)
    batch = parse_game_log(data, "2023-24", "regular", LOG_PROV)
    assert batch.results == ()
    assert sorted((i.kind, i.key) for i in batch.issues) == [
        ("matchup_ambiguous", "0022300061"),
        ("not_final", "0022300062"),
        ("unexpected_game", "0042300111"),
    ]


def test_adapter_sends_through_ingestion_client(
    make_client: Callable[..., IngestionClient],
) -> None:
    """The adapter builds the request from nba_api and the client sends it (D-031)."""
    handler = nba_fixture_handler()
    source = NbaApiScheduleSource(make_client(handler))
    assert isinstance(source, ScheduleSource)
    assert isinstance(source, ResultSource)
    run = FakeRun()

    schedule = source.fetch_season_schedule("2026-27", run)
    results = source.fetch_season_results("2025-26", "playoffs", run)

    first, second = handler.seen  # type: ignore[attr-defined]
    assert first.url.path == "/stats/scheduleleaguev2"
    assert dict(first.url.params) == {"LeagueID": "00", "Season": "2026-27"}
    assert second.url.params["SeasonType"] == "Playoffs"
    assert second.url.params["PlayerOrTeam"] == "T"
    assert run.api_calls == 2
    assert schedule.provenance.source == "scheduleleaguev2"
    assert results.provenance.source == "leaguegamelog"
    assert len(schedule.games) == 5 and len(results.results) == 2


def test_adapter_rejects_unknown_season_type(
    make_client: Callable[..., IngestionClient],
) -> None:
    """Only regular, playin and playoffs exist as result season types."""
    source = NbaApiScheduleSource(make_client(nba_fixture_handler()))
    with pytest.raises(ValueError):
        source.fetch_season_results("2025-26", "preseason")
