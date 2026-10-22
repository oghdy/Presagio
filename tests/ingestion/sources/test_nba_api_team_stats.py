"""nba_api team stats adapters: LeagueGameLog / BoxScoreTraditionalV3 fixtures -> lines.

No DB, no network: parsers get fixture JSON, sources go through IngestionClient with a
MockTransport (D-031).
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from nba_api.library.http import NBAHTTP

from presagio.ingestion.client import IngestionClient
from presagio.ingestion.sources.base import (
    GameTeamStatsSource,
    Provenance,
    SourceFormatError,
    TeamStatsSource,
)
from presagio.ingestion.sources.nba_api.team_stats import (
    NbaApiBoxScoreSource,
    NbaApiTeamStatsSource,
    parse_box_score,
    parse_game_log_team_stats,
)

from ..helpers import FakeRun, edit_log_row, fixture_json, keep_log_games, nba_stats_fixture_handler

LOG_PROV = Provenance("leaguegamelog", datetime(2026, 10, 22, 12, tzinfo=UTC), 2)
BOX_PROV = Provenance("boxscoretraditionalv3", datetime(2026, 10, 22, 12, tzinfo=UTC), 3)
DEN, LAL, GSW, PHX = 1610612743, 1610612747, 1610612744, 1610612756
GAME_LOGS = [
    ("leaguegamelog__2023-24_RegularSeason", "2023-24", "regular"),
    ("leaguegamelog__2023-24_Playoffs", "2023-24", "playoffs"),
    ("leaguegamelog__2024-25_RegularSeason", "2024-25", "regular"),
    ("leaguegamelog__2024-25_Playoffs", "2024-25", "playoffs"),
    ("leaguegamelog__2025-26_RegularSeason", "2025-26", "regular"),
    ("leaguegamelog__2025-26_PlayIn", "2025-26", "playin"),
    ("leaguegamelog__2025-26_Playoffs", "2025-26", "playoffs"),
    ("leaguegamelog__2026-27_RegularSeason", "2026-27", "regular"),
]
LOG_2023 = "leaguegamelog__2023-24_RegularSeason"
BOX_2023 = "boxscoretraditionalv3__0022300061"


@pytest.fixture(autouse=True)
def no_direct_nba_api_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """D-031: nba_api must never send a request itself; only IngestionClient does."""

    def refuse(*args: Any, **kwargs: Any) -> Any:
        """Fail loudly if nba_api tries to send."""
        raise AssertionError("nba_api sent a request itself (get_request must be False)")

    monkeypatch.setattr(NBAHTTP, "send_api_request", refuse)


def log_2023(data: Any | None = None) -> Any:
    """parse_game_log_team_stats on the 2023-24 regular season fixture (or an edited copy)."""
    return parse_game_log_team_stats(
        data if data is not None else fixture_json(LOG_2023), "2023-24", "regular", LOG_PROV
    )


def test_game_log_rows_become_lines_with_source_values() -> None:
    """Every column maps 1:1 from the row (schema 6-2); home from MATCHUP, opponent paired."""
    batch = log_2023()
    den = next(s for s in batch.stats if s.team_id == DEN)
    assert den.game_id == "0022300061"
    assert (den.opponent_team_id, den.is_home, den.minutes) == (LAL, True, 240)
    assert (den.pts, den.fgm, den.fga, den.fg3m, den.fg3a) == (119, 48, 91, 14, 34)
    assert (den.ftm, den.fta, den.oreb, den.dreb, den.reb) == (9, 12, 9, 33, 42)
    assert (den.ast, den.stl, den.blk, den.pf, den.plus_minus) == (29, 9, 6, 15, 12)
    # Game log TOV includes team turnovers (12); the box score says 11. Kept as given (A-6).
    assert den.tov == 12
    lal = next(s for s in batch.stats if s.team_id == LAL)
    assert (lal.opponent_team_id, lal.is_home, lal.pts, lal.plus_minus) == (DEN, False, 107, -12)
    assert (batch.season, batch.season_type, batch.rejected) == ("2023-24", "regular", {})
    assert batch.provenance == LOG_PROV


def test_same_response_carries_the_results() -> None:
    """One parse feeds both loaders: results are schedule.parse_game_log's output."""
    batch = log_2023()
    assert batch.results is not None
    assert [(r.game_id, r.home_team_id, r.home_score) for r in batch.results.results] == [
        ("0022300061", DEN, 119),
        ("0022300062", GSW, 104),
    ]
    assert batch.results.provenance == LOG_PROV


@pytest.mark.parametrize(("name", "season", "season_type"), GAME_LOGS)
def test_every_fixture_gives_two_lines_per_game(name: str, season: str, season_type: str) -> None:
    """Exactly one home and one away line per game, points equal to the results' scores."""
    batch = parse_game_log_team_stats(fixture_json(name), season, season_type, LOG_PROV)
    assert batch.results is not None and len(batch.results.results) == 2
    for result in batch.results.results:
        lines = [s for s in batch.stats if s.game_id == result.game_id]
        assert len(lines) == 2
        home = next(s for s in lines if s.is_home)
        away = next(s for s in lines if not s.is_home)
        assert (home.team_id, away.team_id) == (result.home_team_id, result.away_team_id)
        assert (home.opponent_team_id, away.opponent_team_id) == (away.team_id, home.team_id)
        assert home.pts + away.pts == result.home_score + result.away_score
    assert len(batch.stats) == 4 and batch.rejected == {}


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("FGA", -1, "non-negative integer"),
        ("TOV", None, "non-negative integer"),
        ("REB", "42", "non-negative integer"),
        ("MIN", 0, "team minutes are 0"),
        ("PLUS_MINUS", 4.5, "not a whole number"),
    ],
)
def test_bad_value_rejects_that_game_only(column: str, value: Any, message: str) -> None:
    """A value a CHECK would refuse drops the whole game (both lines); the other is kept."""
    data = fixture_json(LOG_2023)
    edit_log_row(data, "0022300062", PHX, **{column: value})
    batch = log_2023(data)
    assert {s.game_id for s in batch.stats} == {"0022300061"}
    assert len(batch.stats) == 2
    assert list(batch.rejected) == ["0022300062"]
    assert message in batch.rejected["0022300062"]


def test_whole_float_plus_minus_is_accepted() -> None:
    """LeagueGameFinder-style 4.0 is the integer 4."""
    data = fixture_json(LOG_2023)
    edit_log_row(data, "0022300062", PHX, PLUS_MINUS=4.0)
    phx = next(s for s in log_2023(data).stats if s.team_id == PHX)
    assert phx.plus_minus == 4 and isinstance(phx.plus_minus, int)


def test_unusable_games_are_rejected_other_games_ignored() -> None:
    """Not final / one row only -> rejected (failed items); another season's game -> issue only."""
    data = fixture_json(LOG_2023)
    edit_log_row(data, "0022300062", GSW, WL=None)
    batch = log_2023(data)
    assert set(batch.rejected) == {"0022300062"}
    assert batch.rejected["0022300062"].startswith("not_final")

    data = fixture_json(LOG_2023)
    data["resultSets"][0]["rowSet"] = [
        r for r in data["resultSets"][0]["rowSet"] if r[1] != PHX
    ]  # GSW row alone
    assert log_2023(data).rejected["0022300062"].startswith("matchup_ambiguous")

    playoffs = parse_game_log_team_stats(
        fixture_json("leaguegamelog__2023-24_Playoffs"), "2023-24", "regular", LOG_PROV
    )
    assert playoffs.stats == () and playoffs.rejected == {}
    assert playoffs.results is not None
    assert {i.kind for i in playoffs.results.issues} == {"unexpected_game"}


def test_neutral_site_game_keeps_both_lines() -> None:
    """Both rows 'X @ Y' is not ambiguous any more: two lines, nothing rejected."""
    data = fixture_json(LOG_2023)
    edit_log_row(data, "0022300062", GSW, MATCHUP="GSW @ PHX")
    batch = log_2023(data)
    assert batch.rejected == {}
    assert sorted(s.team_id for s in batch.stats if s.game_id == "0022300062") == sorted([GSW, PHX])


def test_team_twice_in_one_game_is_rejected() -> None:
    """Both rows naming the same team cannot become two lines."""
    data = keep_log_games(fixture_json(LOG_2023), "0022300061")
    edit_log_row(data, "0022300061", LAL, TEAM_ID=DEN)
    batch = log_2023(data)
    assert batch.stats == ()
    assert "0022300061" in batch.rejected


def test_missing_stat_column_is_a_format_error() -> None:
    """A header the adapter needs is gone: the whole response is unusable (no guessing)."""
    data = fixture_json(LOG_2023)
    headers = data["resultSets"][0]["headers"]
    headers[headers.index("OREB")] = "OFF_REB"
    with pytest.raises(SourceFormatError, match="OREB"):
        log_2023(data)


def test_box_score_statistics_become_lines() -> None:
    """Team statistics only: '240:00' -> 240, 12.0 -> 12, players' turnovers 11 (A-6)."""
    batch = parse_box_score(fixture_json(BOX_2023), "0022300061", BOX_PROV)
    assert batch.rejected == {} and batch.results is None
    assert batch.season == "2023-24"
    den, lal = batch.stats
    assert (den.team_id, den.opponent_team_id, den.is_home) == (DEN, LAL, True)
    assert (lal.team_id, lal.opponent_team_id, lal.is_home) == (LAL, DEN, False)
    assert (den.minutes, den.pts, den.fga, den.oreb, den.fta, den.tov) == (240, 119, 91, 9, 12, 11)
    assert den.plus_minus == 12 and isinstance(den.plus_minus, int)
    # bench.minutes ("171:48", research 6) is never read.
    assert lal.minutes == 240


@pytest.mark.parametrize("game_id", ["0022600001", "0042500121"])
def test_other_box_score_fixtures(game_id: str) -> None:
    """Both remaining fixtures parse into a home and an away line."""
    batch = parse_box_score(fixture_json(f"boxscoretraditionalv3__{game_id}"), game_id, BOX_PROV)
    assert [s.is_home for s in batch.stats] == [True, False]
    assert batch.rejected == {}


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("minutes", "240:30", "not whole minutes"),
        ("minutes", "", "not 'MMM:SS'"),
        ("minutes", "0:00", "team minutes are 0"),
        ("plusMinusPoints", 12.5, "not a whole number"),
        ("turnovers", -1, "non-negative integer"),
        ("points", None, "non-negative integer"),
    ],
)
def test_bad_box_score_value_rejects_the_game(key: str, value: Any, message: str) -> None:
    """A value a CHECK would refuse -> no lines, game rejected with the reason."""
    data = fixture_json(BOX_2023)
    data["boxScoreTraditional"]["awayTeam"]["statistics"][key] = value
    batch = parse_box_score(data, "0022300061", BOX_PROV)
    assert batch.stats == ()
    assert message in batch.rejected["0022300061"]


def test_box_score_missing_statistics_rejects_the_game() -> None:
    """No statistics block -> rejected, not a crash."""
    data = fixture_json(BOX_2023)
    del data["boxScoreTraditional"]["homeTeam"]["statistics"]
    assert "statistics" in parse_box_score(data, "0022300061", BOX_PROV).rejected["0022300061"]


def test_box_score_for_another_game_is_a_format_error() -> None:
    """The response must be the game we asked for."""
    with pytest.raises(SourceFormatError, match="asked for 0022300062"):
        parse_box_score(fixture_json(BOX_2023), "0022300062", BOX_PROV)


def test_sources_send_through_ingestion_client(
    make_client: Callable[..., IngestionClient],
) -> None:
    """Both adapters satisfy their protocols and send one request each via the client."""
    handler = nba_stats_fixture_handler()
    client = make_client(handler)
    stats_source, box_source = NbaApiTeamStatsSource(client), NbaApiBoxScoreSource(client)
    assert isinstance(stats_source, TeamStatsSource)
    assert isinstance(box_source, GameTeamStatsSource)
    run = FakeRun()

    log = stats_source.fetch_season_team_stats("2025-26", "playin", run)
    box = box_source.fetch_game_team_stats("0042500121", run)

    first, second = handler.seen  # type: ignore[attr-defined]
    assert first.url.path == "/stats/leaguegamelog"
    assert first.url.params["SeasonType"] == "PlayIn"
    assert first.url.params["PlayerOrTeam"] == "T"
    assert second.url.path == "/stats/boxscoretraditionalv3"
    assert second.url.params["GameID"] == "0042500121"
    assert run.api_calls == 2
    assert log.provenance.source == "leaguegamelog" and len(log.stats) == 4
    assert box.provenance.source == "boxscoretraditionalv3" and len(box.stats) == 2


def test_sources_refuse_bad_arguments(make_client: Callable[..., IngestionClient]) -> None:
    """Unknown season type and malformed game IDs fail before any request."""
    handler = nba_stats_fixture_handler()
    client = make_client(handler)
    with pytest.raises(ValueError):
        NbaApiTeamStatsSource(client).fetch_season_team_stats("2025-26", "preseason")
    with pytest.raises(ValueError):
        NbaApiBoxScoreSource(client).fetch_game_team_stats("22300061")
    assert handler.seen == []  # type: ignore[attr-defined]
