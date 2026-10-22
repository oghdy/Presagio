"""games loader: schedule upserts, result updates, guards, end to end through the client.

Every test runs adapter -> IngestionClient(MockTransport with fixtures) -> raw -> loader on
a temp database (D-030). No test compares Python time with the database clock.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, text

from presagio.ingestion.loaders import (
    apply_results,
    load_season_results,
    load_season_schedule,
    load_teams,
    upsert_schedule,
)
from presagio.ingestion.sources.base import Provenance
from presagio.ingestion.sources.nba_api import parse_game_log, parse_schedule

from ..helpers import T0, fixture_json
from .conftest import NbaApiStub

SCHEDULE_2026 = "scheduleleaguev2__2026-27"
LOG_2026 = "leaguegamelog__2026-27_RegularSeason"
PHI, CLE, DET, BOS = 1610612755, 1610612739, 1610612765, 1610612738
RAW_SCHEDULE_COUNT = "SELECT count(*) FROM raw_api_responses WHERE endpoint = 'scheduleleaguev2'"


def scalar(db: Engine, sql: str, **params: Any) -> Any:
    """One value from a query."""
    with db.connect() as conn:
        return conn.execute(text(sql), params).scalar_one()


def game(db: Engine, game_id: str) -> dict[str, Any]:
    """A games row as a dict."""
    with db.connect() as conn:
        row = conn.execute(text("SELECT * FROM games WHERE game_id = :g"), {"g": game_id}).one()
    return dict(row._mapping)


def runs(db: Engine) -> list[dict[str, Any]]:
    """All ingestion_runs rows in order."""
    with db.connect() as conn:
        rows = conn.execute(text("SELECT * FROM ingestion_runs ORDER BY run_id")).all()
    return [dict(r._mapping) for r in rows]


def schedule_game(data: dict[str, Any], game_id: str) -> dict[str, Any]:
    """The game object with this ID inside a ScheduleLeagueV2 response."""
    for day in data["leagueSchedule"]["gameDates"]:
        for g in day["games"]:
            if g["gameId"] == game_id:
                return g
    raise KeyError(game_id)


def log_row(data: dict[str, Any], **values: Any) -> list[Any]:
    """A copy of the first LeagueGameLog row with some columns replaced."""
    headers = data["resultSets"][0]["headers"]
    row = list(data["resultSets"][0]["rowSet"][0])
    for name, value in values.items():
        row[headers.index(name)] = value
    return row


def encode(data: Any) -> bytes:
    """Fixture-like JSON bytes."""
    return json.dumps(data).encode()


@pytest.fixture
def loaded(db: Engine, nba: NbaApiStub) -> NbaApiStub:
    """Teams loaded, 2026-27 schedule loaded once."""
    load_teams(nba.teams, db)
    load_season_schedule(nba.schedule, db, "2026-27")
    return nba


def test_loading_a_season_twice_keeps_row_count(db: Engine, nba: NbaApiStub) -> None:
    """Criterion 2: same fixture twice -> same games rows; only fetched_at moves."""
    load_teams(nba.teams, db)
    first = load_season_schedule(nba.schedule, db, "2026-27")
    assert (first.counts.inserted, first.counts.updated) == (5, 0)
    before = {g: game(db, g) for g in ("0022600001", "0022601201")}

    nba.clock.advance(3600)
    second = load_season_schedule(nba.schedule, db, "2026-27")
    assert (second.counts.inserted, second.counts.updated, second.counts.stale) == (0, 5, 0)
    assert scalar(db, "SELECT count(*) FROM games") == 5
    for game_id, old in before.items():
        new = game(db, game_id)
        assert new["fetched_at"] == nba.clock.now() == old["fetched_at"] + timedelta(hours=1)
        # Same bytes -> same raw row (A-1); every other column is unchanged.
        unchanged = {k: v for k, v in new.items() if k != "fetched_at"}
        assert unchanged == {k: v for k, v in old.items() if k != "fetched_at"}
    assert scalar(db, RAW_SCHEDULE_COUNT) == 1
    schedule_runs = [r for r in runs(db) if r["job_type"] == "schedule"]
    assert [(r["status"], r["scope_key"], r["api_calls"]) for r in schedule_runs] == [
        ("succeeded", "season=2026-27", 1),
        ("succeeded", "season=2026-27", 1),
    ]
    assert scalar(db, "SELECT count(*) FROM ingestion_runs WHERE status = 'running'") == 0


@pytest.mark.parametrize("season", ["2023-24", "2024-25", "2025-26"])
def test_utc_times_and_game_types(db: Engine, nba: NbaApiStub, season: str) -> None:
    """Criterion 3: times stored in UTC, regular/playoffs/playin/cup final from the ID."""
    load_teams(nba.teams, db)
    result = load_season_schedule(nba.schedule, db, season)
    assert result.skipped == {"preseason": 1}
    data = fixture_json(f"scheduleleaguev2__{season}")
    with db.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT game_id, game_type, season, start_time_utc,"
                " to_char(start_time_utc AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')"
                "   AS utc_text,"
                " to_char(start_time_utc AT TIME ZONE 'America/New_York',"
                '   \'YYYY-MM-DD"T"HH24:MI:SS"Z"\') AS et_text'
                " FROM games ORDER BY game_id"
            )
        ).all()
    assert sorted(r.game_type for r in rows) == ["cup_final", "playin", "playoffs", "regular"]
    expected_type = {"2": "regular", "4": "playoffs", "5": "playin", "6": "cup_final"}
    for row in rows:
        source = schedule_game(data, row.game_id)
        assert row.game_type == expected_type[row.game_id[2]]
        assert row.season == season
        assert row.start_time_utc.utcoffset() == timedelta(0)
        assert row.utc_text == source["gameDateTimeUTC"]
        # The fake-Z Eastern field equals our UTC value seen in New York time.
        assert row.et_text == source["gameDateTimeEst"]
    assert not any(r.game_id.startswith("001") for r in rows)
    (run,) = [r for r in runs(db) if r["job_type"] == "schedule"]
    assert run["error_detail"]["skipped_game_types"] == {"preseason": 1}


def test_scheduled_and_final_games(db: Engine, loaded: NbaApiStub) -> None:
    """Criterion 4: scheduled games have NULL scores, finals have scores, TBD has no teams."""
    scheduled = game(db, "0022600006")
    assert (scheduled["status_code"], scheduled["home_score"], scheduled["away_score"]) == (
        1,
        None,
        None,
    )
    final = game(db, "0022600001")
    assert (final["status_code"], final["home_score"], final["away_score"]) == (3, 121, 113)
    tbd = game(db, "0022601201")
    assert tbd["is_time_tbd"] is True
    assert (tbd["home_team_id"], tbd["away_team_id"]) == (None, None)
    cup_final = game(db, "0062600001")
    assert (cup_final["game_type"], cup_final["is_neutral"]) == ("cup_final", True)
    assert cup_final["source"] == "scheduleleaguev2"


def test_scheduled_game_becomes_final_on_schedule_reload(db: Engine, loaded: NbaApiStub) -> None:
    """'Scheduled -> final' fills the scores in place; the TBD game gets its teams and time."""
    data = fixture_json(SCHEDULE_2026)
    g = schedule_game(data, "0022600006")
    g.update(gameStatus=3, gameStatusText="Final/OT")
    g["homeTeam"]["score"], g["awayTeam"]["score"] = 118, 120
    tbd = schedule_game(data, "0022601201")
    tbd.update(gameStatusText="7:30 pm ET", gameDateTimeUTC="2026-12-05T00:30:00Z")
    tbd["homeTeam"]["teamId"], tbd["awayTeam"]["teamId"] = BOS, DET
    loaded.overrides[SCHEDULE_2026] = encode(data)
    loaded.clock.advance(86400)

    result = load_season_schedule(loaded.schedule, db, "2026-27")
    assert (result.counts.inserted, result.counts.updated) == (0, 5)
    assert scalar(db, "SELECT count(*) FROM games") == 5
    now_final = game(db, "0022600006")
    assert (now_final["status_code"], now_final["status_text"]) == (3, "Final/OT")
    assert (now_final["home_score"], now_final["away_score"]) == (118, 120)
    decided = game(db, "0022601201")
    assert decided["is_time_tbd"] is False
    assert (decided["home_team_id"], decided["away_team_id"]) == (BOS, DET)
    assert decided["start_time_utc"] == datetime(2026, 12, 5, 0, 30, tzinfo=UTC)
    # A changed response is a new raw version, and the games now point at it.
    assert scalar(db, RAW_SCHEDULE_COUNT) == 2
    newest = scalar(db, "SELECT max(raw_response_id) FROM raw_api_responses")
    assert now_final["raw_response_id"] == decided["raw_response_id"] == newest


def test_game_log_finishes_a_scheduled_game(db: Engine, loaded: NbaApiStub) -> None:
    """LeagueGameLog sets status and scores only; a game the schedule lacks is a failed item."""
    data = fixture_json(LOG_2026)
    data["resultSets"][0]["rowSet"] += [
        log_row(data, GAME_ID="0022600006", TEAM_ID=PHI, MATCHUP="PHI vs. CLE", WL="W", PTS=110),
        log_row(data, GAME_ID="0022600006", TEAM_ID=CLE, MATCHUP="CLE @ PHI", WL="L", PTS=101),
    ]
    loaded.overrides[LOG_2026] = encode(data)
    loaded.clock.advance(600)
    before = game(db, "0022600006")

    result = load_season_results(loaded.schedule, db, "2026-27", "regular")
    assert result.missing == ("0022600003",)
    assert result.updated == 2  # 0022600001 confirmed, 0022600006 finished
    assert result.issues == ()
    after = game(db, "0022600006")
    assert (after["status_code"], after["status_text"]) == (3, "Final")
    assert (after["home_score"], after["away_score"]) == (110, 101)
    assert after["source"] == "leaguegamelog"
    # Columns the game log does not own keep the schedule's values (6-1 table).
    for column in ("start_time_utc", "postponed_status", "home_team_id", "game_subtype", "season"):
        assert after[column] == before[column]
    assert game(db, "0022600001")["status_text"] == "Final"
    assert scalar(db, "SELECT count(*) FROM games") == 5

    (run,) = [r for r in runs(db) if r["job_type"] == "gamelog"]
    assert run["status"] == "partial"
    assert run["scope_key"] == "season=2026-27;season_type=Regular Season"
    assert run["season_type"] == "Regular Season"
    assert (run["items_total"], run["items_succeeded"], run["items_failed"]) == (3, 2, 1)
    assert run["error_detail"]["failed_items"][0]["key"] == "0022600003"

    # Same game log again: same rows, nothing new.
    loaded.clock.advance(600)
    load_season_results(loaded.schedule, db, "2026-27", "regular")
    assert scalar(db, "SELECT count(*) FROM games") == 5
    assert game(db, "0022600006")["home_score"] == 110


def test_game_log_keeps_overtime_text_of_schedule(db: Engine, nba: NbaApiStub) -> None:
    """A game already final as 'Final/OT2' keeps that text when the log confirms it."""
    load_teams(nba.teams, db)
    load_season_schedule(nba.schedule, db, "2025-26")
    nba.clock.advance(60)
    result = load_season_results(nba.schedule, db, "2025-26", "regular")
    assert result.missing == ("0022500002",)
    assert game(db, "0022500001")["status_text"] == "Final/OT2"
    assert game(db, "0022500001")["source"] == "leaguegamelog"


def test_home_away_disagreement_trusts_schedule(db: Engine, loaded: NbaApiStub) -> None:
    """Log says BOS was home, schedule says DET: issue recorded, scores follow team IDs."""
    data = fixture_json(LOG_2026)
    headers = data["resultSets"][0]["headers"]
    for row in data["resultSets"][0]["rowSet"]:
        if row[headers.index("TEAM_ID")] == DET:
            row[headers.index("MATCHUP")] = "DET @ BOS"
        elif row[headers.index("TEAM_ID")] == BOS:
            row[headers.index("MATCHUP")] = "BOS vs. DET"
    loaded.overrides[LOG_2026] = encode(data)
    loaded.clock.advance(60)

    result = load_season_results(loaded.schedule, db, "2026-27", "regular")
    assert [(i.kind, i.key) for i in result.issues] == [("home_away_mismatch", "0022600001")]
    row = game(db, "0022600001")
    assert (row["home_team_id"], row["away_team_id"]) == (DET, BOS)
    assert (row["home_score"], row["away_score"]) == (121, 113)
    (run,) = [r for r in runs(db) if r["job_type"] == "gamelog"]
    assert run["error_detail"]["quality_issues"][0]["kind"] == "home_away_mismatch"


def test_score_or_team_disagreement_is_reported_not_written(db: Engine, loaded: NbaApiStub) -> None:
    """A different final score or different teams: schedule kept, issue recorded."""
    data = fixture_json(LOG_2026)
    headers = data["resultSets"][0]["headers"]
    for row in data["resultSets"][0]["rowSet"]:
        if row[headers.index("TEAM_ID")] == DET:
            row[headers.index("PTS")] = 99
    data["resultSets"][0]["rowSet"] += [
        log_row(data, GAME_ID="0022600006", TEAM_ID=PHI, MATCHUP="PHI vs. BOS", WL="W", PTS=1),
        log_row(data, GAME_ID="0022600006", TEAM_ID=BOS, MATCHUP="BOS @ PHI", WL="L", PTS=0),
    ]
    loaded.overrides[LOG_2026] = encode(data)
    loaded.clock.advance(60)

    result = load_season_results(loaded.schedule, db, "2026-27", "regular")
    assert sorted((i.kind, i.key) for i in result.issues) == [
        ("score_mismatch", "0022600001"),
        ("team_mismatch", "0022600006"),
    ]
    assert result.updated == 0
    assert game(db, "0022600001")["home_score"] == 121
    assert game(db, "0022600006")["status_code"] == 1


def test_older_response_never_overwrites_newer(db: Engine, loaded: NbaApiStub) -> None:
    """fetched_at guard (1-4): re-processing an old response leaves the newer values."""
    raw_id = game(db, "0022600006")["raw_response_id"]
    data = fixture_json(SCHEDULE_2026)
    g = schedule_game(data, "0022600006")
    g.update(gameStatus=3, gameStatusText="Final")
    g["homeTeam"]["score"], g["awayTeam"]["score"] = 100, 90
    older = Provenance("scheduleleaguev2", T0 - timedelta(days=1), raw_id)
    with db.begin() as conn:
        result = upsert_schedule(conn, parse_schedule(data, "2026-27", older))
    assert (result.counts.inserted, result.counts.updated, result.counts.stale) == (0, 0, 5)
    assert game(db, "0022600006")["status_code"] == 1

    log = fixture_json(LOG_2026)
    log_older = Provenance("leaguegamelog", T0 - timedelta(days=1), raw_id)
    with db.begin() as conn:
        applied = apply_results(conn, parse_game_log(log, "2026-27", "regular", log_older))
    assert (applied.updated, applied.stale) == (0, 1)
    assert game(db, "0022600001")["source"] == "scheduleleaguev2"


def test_game_with_unknown_team_is_a_failed_item(db: Engine, loaded: NbaApiStub) -> None:
    """A team ID missing from teams fails that game only; the run ends partial."""
    data = fixture_json(SCHEDULE_2026)
    schedule_game(data, "0022600006")["homeTeam"]["teamId"] = 1610619999
    loaded.overrides[SCHEDULE_2026] = encode(data)
    loaded.clock.advance(60)
    result = load_season_schedule(loaded.schedule, db, "2026-27")
    assert list(result.failed) == ["0022600006"]
    assert result.counts.updated == 4
    run = runs(db)[-1]
    assert (run["status"], run["items_failed"], run["items_succeeded"]) == ("partial", 1, 4)


def test_full_season_pipeline(db: Engine, nba: NbaApiStub) -> None:
    """Teams -> schedule -> every season type's results for 2025-26, all runs closed."""
    load_teams(nba.teams, db)
    load_season_schedule(nba.schedule, db, "2025-26")
    for season_type in ("regular", "playin", "playoffs"):
        nba.clock.advance(60)
        load_season_results(nba.schedule, db, "2025-26", season_type)
    assert scalar(db, "SELECT count(*) FROM games") == 4
    with db.connect() as conn:
        by_source = dict(
            conn.execute(text("SELECT game_id, source FROM games ORDER BY game_id")).all()
        )
    # The cup final is in no game log (research 3-3), so the schedule still owns it.
    assert by_source == {
        "0022500001": "leaguegamelog",
        "0042500121": "leaguegamelog",
        "0052500111": "leaguegamelog",
        "0062500001": "scheduleleaguev2",
    }
    assert [(r["job_type"], r["status"]) for r in runs(db)] == [
        ("teams", "succeeded"),
        ("schedule", "succeeded"),
        ("gamelog", "partial"),
        ("gamelog", "partial"),
        ("gamelog", "partial"),
    ]
    assert scalar(db, "SELECT sum(api_calls) FROM ingestion_runs") == 5
