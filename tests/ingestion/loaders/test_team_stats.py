"""team_game_stats loader: completion criteria 1, 2 and 4 of P-015, end to end.

Every test runs adapter -> IngestionClient(MockTransport with fixtures) -> raw -> loader on
a temp database (D-030). Game log fixtures list two games each, and only one of them is in
the trimmed schedule fixture, so a plain fixture run ends 'partial' by design (D-034).
No test compares Python time with the database clock.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, text

from presagio.ingestion.client import IngestionClient, db_raw_store
from presagio.ingestion.loaders import load_season_schedule, load_teams
from presagio.ingestion.loaders.team_stats import (
    load_game_box_score,
    load_season_game_log,
    upsert_team_stats,
)
from presagio.ingestion.sources.base import Provenance
from presagio.ingestion.sources.nba_api import NbaApiScheduleSource, NbaApiTeamSource
from presagio.ingestion.sources.nba_api.team_stats import (
    NbaApiBoxScoreSource,
    NbaApiTeamStatsSource,
    parse_game_log_team_stats,
)

from ..helpers import (
    FakeClock,
    Handler,
    edit_log_row,
    fixture_json,
    keep_log_games,
    nba_stats_fixture_handler,
)

SEASONS = ("2023-24", "2024-25", "2025-26", "2026-27")
GAME_LOGS = [
    ("2023-24", "regular"),
    ("2023-24", "playoffs"),
    ("2024-25", "regular"),
    ("2024-25", "playoffs"),
    ("2025-26", "regular"),
    ("2025-26", "playin"),
    ("2025-26", "playoffs"),
    ("2026-27", "regular"),
]
# Game log games that are also in the trimmed schedule fixtures (the rest are not).
IN_SCHEDULE = {
    "0022300061",
    "0042300111",
    "0022400061",
    "0042400121",
    "0022500001",
    "0052500111",
    "0042500121",
    "0022600001",
}
NOT_IN_SCHEDULE = {
    "0022300062",
    "0042300131",
    "0022400062",
    "0042400171",
    "0022500002",
    "0052500121",
    "0042500131",
    "0022600003",
}
LOG_2026 = "leaguegamelog__2026-27_RegularSeason"
LOG_2023_REG = "leaguegamelog__2023-24_RegularSeason"
DET, BOS, PHI, DEN, LAL = 1610612765, 1610612738, 1610612755, 1610612743, 1610612747


@dataclass
class StatsStub:
    """All nba_api sources over one client serving fixtures (box scores included)."""

    teams: NbaApiTeamSource
    schedule: NbaApiScheduleSource
    stats: NbaApiTeamStatsSource
    box: NbaApiBoxScoreSource
    handler: Handler
    clock: FakeClock
    overrides: dict[str, bytes] = field(default_factory=dict)

    def requests_to(self, endpoint: str) -> int:
        """How many requests went to this stats.nba.com endpoint."""
        seen = self.handler.seen  # type: ignore[attr-defined]
        return sum(1 for r in seen if r.url.path.endswith(f"/{endpoint}"))


@pytest.fixture
def nba(db: Engine, make_client: Callable[..., IngestionClient], clock: FakeClock) -> StatsStub:
    """Adapters -> IngestionClient -> MockTransport(fixtures), raw stored in the temp DB."""
    overrides: dict[str, bytes] = {}
    handler = nba_stats_fixture_handler(overrides)
    client = make_client(handler, store=db_raw_store(db))
    return StatsStub(
        NbaApiTeamSource(client),
        NbaApiScheduleSource(client),
        NbaApiTeamStatsSource(client),
        NbaApiBoxScoreSource(client),
        handler,
        clock,
        overrides,
    )


@pytest.fixture
def scheduled(db: Engine, nba: StatsStub) -> StatsStub:
    """Teams and all four schedule fixtures loaded (games exist before their stats)."""
    load_teams(nba.teams, db)
    for season in SEASONS:
        load_season_schedule(nba.schedule, db, season)
    return nba


def load_all_game_logs(db: Engine, nba: StatsStub) -> None:
    """Every game log fixture once, through the combined run."""
    for season, season_type in GAME_LOGS:
        load_season_game_log(nba.stats, db, season, season_type)


def rows(db: Engine, sql: str, **params: Any) -> list[dict[str, Any]]:
    """Query result as dicts."""
    with db.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(text(sql), params).all()]


def scalar(db: Engine, sql: str, **params: Any) -> Any:
    """One value from a query."""
    with db.connect() as conn:
        return conn.execute(text(sql), params).scalar_one()


def gamelog_runs(db: Engine) -> list[dict[str, Any]]:
    """gamelog ingestion_runs rows in order."""
    return rows(db, "SELECT * FROM ingestion_runs WHERE job_type = 'gamelog' ORDER BY run_id")


def issue_kinds(run: dict[str, Any]) -> list[tuple[str, str]]:
    """(kind, key) of every quality issue recorded on a run."""
    issues = (run["error_detail"] or {}).get("quality_issues", [])
    return [(i["kind"], i["key"]) for i in issues]


def override_log(nba: StatsStub, data: Any, name: str = LOG_2026) -> None:
    """Serve edited game log JSON instead of the fixture from now on."""
    nba.overrides[name] = json.dumps(data).encode()


def test_two_team_rows_per_game(db: Engine, scheduled: StatsStub) -> None:
    """Criterion 1: every stored game has exactly two rows, one home and one away."""
    load_all_game_logs(db, scheduled)
    per_game = rows(
        db,
        "SELECT game_id, count(*) AS n, count(*) FILTER (WHERE is_home) AS homes,"
        " count(DISTINCT team_id) AS teams FROM team_game_stats GROUP BY game_id",
    )
    assert {r["game_id"] for r in per_game} == IN_SCHEDULE
    assert all((r["n"], r["homes"], r["teams"]) == (2, 1, 2) for r in per_game)
    assert scalar(db, "SELECT count(DISTINCT source) FROM team_game_stats") == 1
    # Each row's teams are the game's two teams, and opponents point at each other.
    mismatched = scalar(
        db,
        "SELECT count(*) FROM team_game_stats s JOIN games g USING (game_id)"
        " WHERE s.team_id <> CASE WHEN s.is_home THEN g.home_team_id ELSE g.away_team_id END"
        "    OR s.opponent_team_id"
        "       <> CASE WHEN s.is_home THEN g.away_team_id ELSE g.home_team_id END",
    )
    assert mismatched == 0


def test_points_add_up_to_final_scores(db: Engine, scheduled: StatsStub) -> None:
    """Criterion 2: SUM(pts) per game = home_score + away_score (and per team too)."""
    load_all_game_logs(db, scheduled)
    sums = rows(
        db,
        "SELECT g.game_id, g.home_score + g.away_score AS final, sum(s.pts) AS points,"
        " sum(s.pts) FILTER (WHERE s.is_home) AS home_pts, g.home_score"
        " FROM games g JOIN team_game_stats s USING (game_id)"
        " GROUP BY g.game_id, g.home_score, g.away_score",
    )
    assert len(sums) == len(IN_SCHEDULE)
    assert all(r["points"] == r["final"] and r["home_pts"] == r["home_score"] for r in sums)
    for run in gamelog_runs(db):
        assert not [k for k in issue_kinds(run) if k[0] == "points_mismatch"]


def test_loading_twice_keeps_row_count(db: Engine, scheduled: StatsStub) -> None:
    """Criterion 4: every game log twice -> same rows; only fetched_at moves."""
    load_all_game_logs(db, scheduled)
    before = rows(db, "SELECT * FROM team_game_stats ORDER BY game_id, team_id, source")
    raw_before = scalar(db, "SELECT count(*) FROM raw_api_responses")
    assert len(before) == 2 * len(IN_SCHEDULE)

    scheduled.clock.advance(3600)
    load_all_game_logs(db, scheduled)
    after = rows(db, "SELECT * FROM team_game_stats ORDER BY game_id, team_id, source")
    assert len(after) == len(before)
    for old, new in zip(before, after, strict=True):
        assert new["fetched_at"] > old["fetched_at"]
        assert {k: v for k, v in new.items() if k != "fetched_at"} == {
            k: v for k, v in old.items() if k != "fetched_at"
        }
    # Same bytes -> the same raw rows (A-1).
    assert scalar(db, "SELECT count(*) FROM raw_api_responses") == raw_before
    second = gamelog_runs(db)[len(GAME_LOGS) :]
    assert all(r["error_detail"]["team_game_stats"]["inserted"] == 0 for r in second)
    assert sum(r["error_detail"]["team_game_stats"]["updated"] for r in second) == 16


def test_one_call_feeds_games_and_team_stats(db: Engine, scheduled: StatsStub) -> None:
    """One LeagueGameLog request per run updates games and writes stats; no box scores."""
    load_season_game_log(scheduled.stats, db, "2026-27", "regular")
    assert scheduled.requests_to("leaguegamelog") == 1
    assert scheduled.requests_to("boxscoretraditionalv3") == 0
    (run,) = gamelog_runs(db)
    (raw_id,) = [
        r["raw_response_id"]
        for r in rows(db, "SELECT * FROM raw_api_responses WHERE endpoint = 'leaguegamelog'")
    ]
    game = rows(db, "SELECT * FROM games WHERE game_id = '0022600001'")[0]
    assert (game["source"], game["raw_response_id"]) == ("leaguegamelog", raw_id)
    stats = rows(db, "SELECT * FROM team_game_stats")
    assert {(s["source"], s["raw_response_id"]) for s in stats} == {("leaguegamelog", raw_id)}
    assert all(s["fetched_at"] == game["fetched_at"] for s in stats)
    assert (run["api_calls"], run["scope_key"], run["season_type"]) == (
        1,
        "season=2026-27;season_type=Regular Season",
        "Regular Season",
    )
    # 0022600003 is not in the trimmed schedule: a failed item, so the run is partial.
    assert (run["status"], run["items_total"], run["items_succeeded"], run["items_failed"]) == (
        "partial",
        2,
        1,
        1,
    )
    assert run["error_detail"]["failed_items"] == [
        {"key": "0022600003", "error": "game not in games yet; load the schedule first"}
    ]
    assert run["rows_written"] == 1 + 2  # one games update + two stat rows
    assert run["error_detail"]["games"] == {
        "updated": 1,
        "skipped": {"stale": 0, "conflicting": 0},
    }


def test_clean_run_succeeds(db: Engine, scheduled: StatsStub) -> None:
    """Only games the schedule knows: succeeded, no failed items, no issues."""
    override_log(scheduled, keep_log_games(fixture_json(LOG_2026), "0022600001"))
    load_season_game_log(scheduled.stats, db, "2026-27", "regular")
    (run,) = gamelog_runs(db)
    assert (run["status"], run["items_total"], run["items_succeeded"]) == ("succeeded", 1, 1)
    assert issue_kinds(run) == []


def test_points_mismatch_is_a_quality_issue(db: Engine, scheduled: StatsStub) -> None:
    """Team PTS not matching games: rows stored as given, issue in error_detail."""
    data = keep_log_games(fixture_json(LOG_2026), "0022600001")
    edit_log_row(data, "0022600001", BOS, PTS=110)
    override_log(scheduled, data)
    result = load_season_game_log(scheduled.stats, db, "2026-27", "regular")
    assert result.stats.stored == ("0022600001",)
    assert scalar(db, "SELECT sum(pts) FROM team_game_stats") == 121 + 110
    # games keeps the schedule's final 121-113 (D-034).
    assert scalar(db, "SELECT away_score FROM games WHERE game_id = '0022600001'") == 113
    (run,) = gamelog_runs(db)
    assert run["status"] == "succeeded"
    assert ("points_mismatch", "0022600001") in issue_kinds(run)
    assert ("score_mismatch", "0022600001") in issue_kinds(run)
    (issue,) = [i for i in run["error_detail"]["quality_issues"] if i["kind"] == "points_mismatch"]
    assert "sum 234" in issue["message"] and "sum 231" in issue["message"]


def test_check_violation_skips_that_game_only(db: Engine, scheduled: StatsStub) -> None:
    """A value a CHECK would refuse: that game is a failed item, nothing half-stored."""
    data = fixture_json("leaguegamelog__2023-24_RegularSeason")
    edit_log_row(data, "0022300061", LAL, FGA=-3)
    override_log(scheduled, data, "leaguegamelog__2023-24_RegularSeason")
    load_season_game_log(scheduled.stats, db, "2023-24", "regular")
    assert scalar(db, "SELECT count(*) FROM team_game_stats") == 0
    (run,) = gamelog_runs(db)
    assert (run["status"], run["items_total"], run["items_failed"]) == ("failed", 2, 2)
    failed = {i["key"]: i["error"] for i in run["error_detail"]["failed_items"]}
    assert "non-negative integer" in failed["0022300061"]
    # Its final score is still applied to games (the result itself was fine).
    assert scalar(db, "SELECT source FROM games WHERE game_id = '0022300061'") == "leaguegamelog"

    # Team minutes 0 (ck_team_game_stats_minutes_positive) in one season type; the untouched
    # 2025-26 fixture still loads its game.
    data = fixture_json(LOG_2026)
    edit_log_row(data, "0022600001", BOS, MIN=0)
    override_log(scheduled, data)
    load_season_game_log(scheduled.stats, db, "2026-27", "regular")
    load_season_game_log(scheduled.stats, db, "2025-26", "regular")
    assert {r["game_id"] for r in rows(db, "SELECT game_id FROM team_game_stats")} == {"0022500001"}
    runs = gamelog_runs(db)
    assert [r["status"] for r in runs[1:]] == ["failed", "partial"]
    assert "team minutes are 0" in runs[1]["error_detail"]["failed_items"][0]["error"]


def test_partial_when_one_of_two_usable_games_breaks(db: Engine, scheduled: StatsStub) -> None:
    """Two games in games, one with a bad value: one stored, one failed -> partial."""
    data = fixture_json(LOG_2026)
    # Rename 0022600003 (not in the schedule) to 0022600002, which the schedule has
    # as PHI (away) at 1610612752; make its rows those teams.
    edit_log_row(data, "0022600003", 1610612760, GAME_ID="0022600002", TEAM_ID=PHI)
    edit_log_row(data, "0022600003", 1610612759, GAME_ID="0022600002", TEAM_ID=1610612752)
    edit_log_row(data, "0022600002", PHI, PTS=106, TOV=-1, MATCHUP="PHI @ NYK")
    edit_log_row(data, "0022600002", 1610612752, PTS=100, MATCHUP="NYK vs. PHI")
    override_log(scheduled, data)
    load_season_game_log(scheduled.stats, db, "2026-27", "regular")
    (run,) = gamelog_runs(db)
    assert (run["status"], run["items_succeeded"], run["items_failed"]) == ("partial", 1, 1)
    assert run["error_detail"]["failed_items"][0]["key"] == "0022600002"
    assert scalar(db, "SELECT count(*) FROM team_game_stats") == 2


def test_game_missing_from_games_fails_without_rows(db: Engine, nba: StatsStub) -> None:
    """FK order (D-034): no schedule loaded -> nothing stored, run failed, no exception."""
    load_teams(nba.teams, db)
    load_season_game_log(nba.stats, db, "2026-27", "regular")
    assert scalar(db, "SELECT count(*) FROM team_game_stats") == 0
    (run,) = gamelog_runs(db)
    assert (run["status"], run["items_failed"]) == ("failed", 2)


def test_team_mismatch_fails_the_game(db: Engine, scheduled: StatsStub) -> None:
    """Teams other than the schedule's: not stored, failed item, issue recorded."""
    data = keep_log_games(fixture_json(LOG_2026), "0022600001")
    edit_log_row(data, "0022600001", BOS, TEAM_ID=PHI, MATCHUP="PHI @ DET")
    override_log(scheduled, data)
    load_season_game_log(scheduled.stats, db, "2026-27", "regular")
    assert scalar(db, "SELECT count(*) FROM team_game_stats") == 0
    (run,) = gamelog_runs(db)
    assert run["status"] == "failed"
    assert ("team_mismatch", "0022600001") in issue_kinds(run)
    assert run["error_detail"]["failed_items"][0]["error"].startswith("team_mismatch")


def test_unknown_team_fails_the_game(db: Engine, scheduled: StatsStub) -> None:
    """A team ID missing from teams would break the FK: failed item instead."""
    data = keep_log_games(fixture_json(LOG_2026), "0022600001")
    edit_log_row(data, "0022600001", BOS, TEAM_ID=1610612799, MATCHUP="XXX @ DET")
    override_log(scheduled, data)
    load_season_game_log(scheduled.stats, db, "2026-27", "regular")
    assert scalar(db, "SELECT count(*) FROM team_game_stats") == 0
    (run,) = gamelog_runs(db)
    assert "not in teams" in run["error_detail"]["failed_items"][0]["error"]


def test_home_and_away_follow_the_schedule(db: Engine, scheduled: StatsStub) -> None:
    """MATCHUP says BOS is home, the schedule says DET: is_home follows games, issue noted."""
    data = keep_log_games(fixture_json(LOG_2026), "0022600001")
    edit_log_row(data, "0022600001", DET, MATCHUP="DET @ BOS")
    edit_log_row(data, "0022600001", BOS, MATCHUP="BOS vs. DET")
    override_log(scheduled, data)
    load_season_game_log(scheduled.stats, db, "2026-27", "regular")
    home = rows(db, "SELECT team_id, pts FROM team_game_stats WHERE is_home")
    assert home == [{"team_id": DET, "pts": 121}]
    (run,) = gamelog_runs(db)
    assert ("home_away_mismatch", "0022600001") in issue_kinds(run)


def test_neutral_site_game_follows_the_schedule(db: Engine, scheduled: StatsStub) -> None:
    """Both rows 'X @ Y': stored with home/away and scores from the schedule, run succeeded."""
    data = keep_log_games(fixture_json(LOG_2023_REG), "0022300061")
    edit_log_row(data, "0022300061", DEN, MATCHUP="DEN @ LAL")
    override_log(scheduled, data, LOG_2023_REG)
    load_season_game_log(scheduled.stats, db, "2023-24", "regular")
    lines = rows(db, "SELECT team_id, is_home, pts FROM team_game_stats ORDER BY team_id")
    assert lines == [
        {"team_id": DEN, "is_home": True, "pts": 119},
        {"team_id": LAL, "is_home": False, "pts": 107},
    ]
    game = rows(
        db,
        "SELECT home_team_id, home_score, away_score FROM games WHERE game_id = :g",
        g="0022300061",
    )
    assert game == [{"home_team_id": DEN, "home_score": 119, "away_score": 107}]
    (run,) = gamelog_runs(db)
    assert run["status"] == "succeeded" and run["items_failed"] == 0
    assert ("neutral_site", "0022300061") in issue_kinds(run)


def test_older_response_does_not_overwrite(db: Engine, scheduled: StatsStub) -> None:
    """fetched_at guard: re-loading an older parse leaves newer rows alone (stale)."""
    load_season_game_log(scheduled.stats, db, "2026-27", "regular")
    stored = rows(db, "SELECT * FROM team_game_stats ORDER BY team_id")
    raw_id, fetched_at = stored[0]["raw_response_id"], stored[0]["fetched_at"]
    data = fixture_json(LOG_2026)
    edit_log_row(data, "0022600001", DET, AST=1)
    old = Provenance("leaguegamelog", fetched_at - timedelta(days=1), raw_id)
    batch = parse_game_log_team_stats(data, "2026-27", "regular", old)
    with db.begin() as conn:
        result = upsert_team_stats(conn, batch)
    assert (result.counts.inserted, result.counts.updated, result.counts.stale) == (0, 0, 2)
    assert result.stale == ("0022600001",) and result.stored == ()
    assert rows(db, "SELECT * FROM team_game_stats ORDER BY team_id") == stored


def test_box_score_is_a_separate_source(db: Engine, scheduled: StatsStub) -> None:
    """Box score rows sit next to game log rows; the primary view picks the game log (A-6)."""
    load_season_game_log(scheduled.stats, db, "2023-24", "regular")
    load_game_box_score(scheduled.box, db, "0022300061")
    load_game_box_score(scheduled.box, db, "0022300061")  # idempotent too
    per_source = rows(
        db,
        "SELECT source, team_id, tov, pts FROM team_game_stats"
        " WHERE game_id = '0022300061' ORDER BY source, team_id",
    )
    assert per_source == [
        {"source": "boxscoretraditionalv3", "team_id": DEN, "tov": 11, "pts": 119},
        {"source": "boxscoretraditionalv3", "team_id": LAL, "tov": 11, "pts": 107},
        {"source": "leaguegamelog", "team_id": DEN, "tov": 12, "pts": 119},
        {"source": "leaguegamelog", "team_id": LAL, "tov": 12, "pts": 107},
    ]
    primary = rows(
        db,
        "SELECT source, tov FROM team_game_stats_primary WHERE game_id = '0022300061'",
    )
    assert primary == [{"source": "leaguegamelog", "tov": 12}] * 2
    box_runs = rows(db, "SELECT * FROM ingestion_runs WHERE job_type = 'boxscore'")
    assert [(r["status"], r["scope_key"], r["api_calls"], r["season"]) for r in box_runs] == [
        ("succeeded", "game=0022300061", 1, "2023-24")
    ] * 2
    assert box_runs[1]["error_detail"]["team_game_stats"] == {
        "inserted": 0,
        "updated": 2,
        "stale": 0,
    }
    assert box_runs[0]["error_detail"].get("quality_issues") is None


def test_box_score_for_unknown_game_fails(db: Engine, nba: StatsStub) -> None:
    """Box score of a game not in games: failed run, nothing stored."""
    load_teams(nba.teams, db)
    load_game_box_score(nba.box, db, "0022600001")
    assert scalar(db, "SELECT count(*) FROM team_game_stats") == 0
    (run,) = rows(db, "SELECT * FROM ingestion_runs WHERE job_type = 'boxscore'")
    assert (run["status"], run["items_total"], run["items_failed"]) == ("failed", 1, 1)
