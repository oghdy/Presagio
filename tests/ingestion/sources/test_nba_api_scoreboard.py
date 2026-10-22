"""nba_api ScoreboardV3 adapter: fixture responses -> GameRecords (no DB, no network)."""

import copy
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest
from nba_api.library.http import NBAHTTP

from presagio.ingestion.client import IngestionClient
from presagio.ingestion.sources import SourceFormatError
from presagio.ingestion.sources.base import Provenance
from presagio.ingestion.sources.nba_api.scoreboard import (
    NbaApiScoreboardSource,
    et_date_bounds,
    parse_scoreboard,
    season_of_et_date,
)

from ..helpers import FakeRun, fixture_bytes, fixture_json, json_bytes

PROV = Provenance("scoreboardv3", datetime(2026, 10, 22, 12, tzinfo=UTC), 1)
OCT22 = date(2026, 10, 22)
MAR05 = date(2026, 3, 5)


@pytest.fixture(autouse=True)
def no_direct_nba_api_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """D-031: nba_api must never send a request itself; only IngestionClient does."""

    def refuse(*args: Any, **kwargs: Any) -> Any:
        """Fail loudly if nba_api tries to send."""
        raise AssertionError("nba_api sent a request itself (get_request must be False)")

    monkeypatch.setattr(NBAHTTP, "send_api_request", refuse)


def board(name: str = "scoreboardv3__2026-10-22") -> dict[str, Any]:
    """A parsed copy of a scoreboard fixture."""
    return fixture_json(name)


def game(data: dict[str, Any], game_id: str) -> dict[str, Any]:
    """One game object of a scoreboard response."""
    return next(g for g in data["scoreboard"]["games"] if g["gameId"] == game_id)


def test_scheduled_games_have_no_scores_and_season_from_id() -> None:
    """2026-10-22 fixture: two scheduled games, 0 points stored as NULL, season from the ID."""
    batch = parse_scoreboard(board(), OCT22, PROV)
    assert [g.game_id for g in batch.games] == ["0022600006", "0022600007"]
    first = batch.games[0]
    assert first.season == "2026-27"
    assert first.status_code == 1 and first.status_text == "7:00 pm ET"
    assert first.home_score is None and first.away_score is None
    assert first.start_time_utc == datetime(2026, 10, 22, 23, 0, tzinfo=UTC)
    assert first.home_team_id == 1610612755 and first.away_team_id == 1610612739
    # The scoreboard has no postponedStatus: None, which the loader never writes over a value.
    assert first.postponed_status is None
    assert first.game_subtype is None and first.is_neutral is False and first.if_necessary is False
    assert batch.season == "2026-27" and batch.provenance == PROV
    assert not batch.rejected and not batch.issues and not batch.skipped


def test_final_games_keep_scores_and_overtime_text() -> None:
    """2026-03-05 fixture: nine final games with scores; 'Final/OT' text is kept as given."""
    batch = parse_scoreboard(board("scoreboardv3__2026-03-05"), MAR05, PROV)
    assert len(batch.games) == 9
    assert all(g.status_code == 3 and g.home_score is not None for g in batch.games)
    by_id = {g.game_id: g for g in batch.games}
    assert by_id["0022500899"].home_score == 115 and by_id["0022500899"].away_score == 114
    assert by_id["0022500902"].status_text == "Final/OT"
    assert {g.season for g in batch.games} == {"2025-26"}
    assert not batch.issues  # every game starts inside ET 2026-03-05


def test_response_for_another_date_is_a_format_error() -> None:
    """Asking for 10-21 and getting gameDate 10-22 means the call itself is wrong."""
    with pytest.raises(SourceFormatError, match="asked for 2026-10-21"):
        parse_scoreboard(board(), date(2026, 10, 21), PROV)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"awayTeam": {"teamId": 0}}, "only one team"),
        ({"awayTeam": {"teamId": 1610612755}}, "same team"),
        ({"gameStatus": 3, "homeTeam": {"score": None}}, "non-negative score"),
        ({"gameTimeUTC": "10/22/2026 23:00"}, "not an ISO time"),
        ({"gameStatus": 7}, "unknown gameStatus"),
        ({"isNeutral": "maybe"}, "expected a boolean"),
    ],
)
def test_one_broken_game_is_rejected_alone(changes: dict[str, Any], message: str) -> None:
    """A game the games CHECKs would refuse is rejected with a reason; the other is kept."""
    data = board()
    target = game(data, "0022600006")
    for key, value in changes.items():
        if isinstance(value, dict):
            target[key].update(value)
        else:
            target[key] = value
    batch = parse_scoreboard(data, OCT22, PROV)
    assert [g.game_id for g in batch.games] == ["0022600007"]
    assert message in batch.rejected["0022600006"]


def test_missing_field_rejects_the_game_without_guessing() -> None:
    """No default for a missing field (CLAUDE.md principle 5)."""
    data = board()
    del game(data, "0022600007")["ifNecessary"]
    batch = parse_scoreboard(data, OCT22, PROV)
    assert "missing field 'ifNecessary'" in batch.rejected["0022600007"]


def test_preseason_and_allstar_are_skipped_and_counted() -> None:
    """Types we never store (A-4) are counted, not loaded."""
    data = board()
    extra = copy.deepcopy(game(data, "0022600006"))
    extra["gameId"] = "0012600001"
    data["scoreboard"]["games"].append(extra)
    batch = parse_scoreboard(data, OCT22, PROV)
    assert batch.skipped == {"preseason": 1}
    assert len(batch.games) == 2


def test_tbd_and_out_of_window_times() -> None:
    """TBD games keep the placeholder time; a real time off the ET date is an issue only."""
    data = board()
    tbd = game(data, "0022600006")
    tbd.update(gameStatusText="TBD", gameTimeUTC="2026-10-22T04:00:00Z")  # ET midnight (EDT)
    late = game(data, "0022600007")
    late["gameTimeUTC"] = "2026-10-23T04:30:00Z"  # 00:30 ET on 10-23
    batch = parse_scoreboard(data, OCT22, PROV)
    by_id = {g.game_id: g for g in batch.games}
    assert by_id["0022600006"].is_time_tbd is True
    assert [(i.kind, i.key) for i in batch.issues] == [("time_outside_date", "0022600007")]


def test_duplicate_game_keeps_the_last_listing() -> None:
    """One upsert cannot touch a key twice: the last listing wins, with an issue."""
    data = board()
    again = copy.deepcopy(game(data, "0022600006"))
    again["gameStatusText"] = "7:30 pm ET"
    data["scoreboard"]["games"].append(again)
    batch = parse_scoreboard(data, OCT22, PROV)
    assert [g.status_text for g in batch.games if g.game_id == "0022600006"] == ["7:30 pm ET"]
    assert [i.kind for i in batch.issues] == ["duplicate_game"]


def test_empty_day_gets_the_season_of_its_date() -> None:
    """No games (off day): the batch season comes from the date (Oct 15 window)."""
    data = board()
    data["scoreboard"]["games"] = []
    batch = parse_scoreboard(data, OCT22, PROV)
    assert batch.games == () and batch.season == "2026-27"
    assert season_of_et_date(date(2026, 10, 14)) == "2025-26"
    assert season_of_et_date(date(2026, 10, 15)) == "2026-27"
    assert season_of_et_date(date(2027, 3, 1)) == "2026-27"


@pytest.mark.parametrize(
    ("day", "lower", "hours"),
    [
        (date(2026, 10, 22), datetime(2026, 10, 22, 4, tzinfo=UTC), 24),  # EDT
        (date(2026, 1, 15), datetime(2026, 1, 15, 5, tzinfo=UTC), 24),  # EST
        (date(2026, 3, 8), datetime(2026, 3, 8, 5, tzinfo=UTC), 23),  # DST starts 02:00
        (date(2026, 11, 1), datetime(2026, 11, 1, 4, tzinfo=UTC), 25),  # DST ends 02:00
    ],
)
def test_et_date_window_follows_daylight_saving(day: date, lower: datetime, hours: int) -> None:
    """An ET date is [00:00 ET, next 00:00 ET) in UTC: 23 or 25 hours on switch days."""
    start, end = et_date_bounds(day)
    assert start == lower
    assert end - start == timedelta(hours=hours)


def test_fetch_sends_game_date_through_the_client(
    make_client: Callable[..., IngestionClient],
) -> None:
    """ScoreboardV3(GameDate=YYYY-MM-DD, LeagueID=00) goes through IngestionClient (D-031)."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """Serve the 2026-10-22 fixture."""
        seen.append(request)
        return json_bytes(200, fixture_bytes("scoreboardv3__2026-10-22"))

    run = FakeRun()
    source = NbaApiScoreboardSource(make_client(handler))
    batch = source.fetch_scoreboard(OCT22, run)
    (request,) = seen
    assert request.url.host == "stats.nba.com" and request.url.path == "/stats/scoreboardv3"
    assert dict(request.url.params) == {"GameDate": "2026-10-22", "LeagueID": "00"}
    assert run.api_calls == 1
    assert batch.provenance.source == "scoreboardv3"
    assert len(batch.games) == 2
