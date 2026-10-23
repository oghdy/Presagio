"""Quality checks on a temp DB (D-030): the baseline passes, broken data gives exact IDs.

Each breaking test edits rows (or drops a CHECK, or replaces a view) inside a transaction that
is rolled back, then runs the checks on that same connection. No network, and no test compares
Python time with the database clock.
"""

import json
from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, text

from presagio.quality import checks
from presagio.quality.checks import (
    CHECK_NAMES,
    ERROR,
    FAIL,
    PASS,
    SKIP,
    WARN,
    CheckResult,
    CheckSettings,
    Outcome,
    QualityReport,
    run_checks,
)

from .conftest import CUP_FINAL, DAYS, FIRST_TIP, ODDS_EVENT, PLAYOFF_GAMES, Baseline, insert_stats

REQUIRED = (  # completion criterion 1 of P-020
    "team_rows_per_game",
    "points_match_score",
    "final_scores_present",
    "regular_season_games",
    "one_game_per_team_day",
)


def one(conn: Connection, name: str, **kwargs: Any) -> CheckResult:
    """Run a single check on the connection and return its row."""
    report = run_checks(conn, checks=[name], **kwargs)
    (result,) = report.results
    return result


def sql(conn: Connection, statement: str, **params: Any) -> None:
    """Execute a statement in the test transaction."""
    conn.execute(text(statement), params)


def first_odds_event(conn: Connection) -> int:
    """odds_event_id of the baseline event."""
    return conn.execute(
        text("SELECT odds_event_id FROM odds_events WHERE source_event_id = :e"), {"e": ODDS_EVENT}
    ).scalar_one()


# Without a recompute function (the feature module, P-035) feature_drift has nothing to compare.
NOTHING_TO_COMPARE = {"feature_drift": SKIP}


def test_baseline_passes_every_check(conn: Connection, baseline: Baseline) -> None:
    """Clean data: every check passes (feature_drift skips), so the exit code is 0."""
    report = run_checks(conn)
    assert [r.name for r in report.results] == list(CHECK_NAMES)
    expected = dict.fromkeys(CHECK_NAMES, PASS) | NOTHING_TO_COMPARE
    assert {r.name: r.status for r in report.results} == expected
    assert report.exit_code == 0
    checked = {r.name: r.checked for r in report.results}
    assert checked["team_rows_per_game"] == 1232  # 1,230 regular + 2 playoffs, not the Cup final
    assert checked["points_match_score"] == 1232
    assert checked["final_scores_present"] == 1233
    assert checked["stat_arithmetic"] == 2 * 1232
    assert checked["regular_season_games"] == 1
    assert checked["one_game_per_team_day"] == 2 * 1233
    assert checked["gamelog_vs_schedule_score"] == 1232
    assert checked["teams_and_aliases"] == 30
    assert checked["odds_after_start"] == 1
    assert checked["recent_failed_runs"] == 1
    # Empty modeling tables (migration 0002) pass with nothing checked.
    assert checked["features_after_tipoff"] == checked["predictions_after_tipoff"] == 0


def test_at_least_the_five_required_checks_exist() -> None:
    """Completion criterion 1: the five named checks are there, plus extras."""
    assert set(REQUIRED) <= set(CHECK_NAMES)
    assert len(CHECK_NAMES) >= 5


def test_missing_stat_row_fails_with_the_game_id(conn: Connection, baseline: Baseline) -> None:
    """A final game with one stat row fails check 1 and is left out of check 2."""
    game_id = baseline.regular_games[10]
    sql(
        conn,
        "DELETE FROM team_game_stats WHERE game_id = :g AND is_home",
        g=game_id,
    )
    result = one(conn, "team_rows_per_game")
    assert (result.status, result.failed, result.examples) == (FAIL, 1, (game_id,))
    assert result.passed == 1231
    assert one(conn, "points_match_score").checked == 1231


def test_home_flag_on_the_wrong_team_fails(conn: Connection, baseline: Baseline) -> None:
    """Two rows but the away team marked home: still not 'two rows, one home'."""
    game_id = baseline.regular_games[20]
    sql(conn, "UPDATE team_game_stats SET is_home = true WHERE game_id = :g", g=game_id)
    result = one(conn, "team_rows_per_game")
    assert (result.status, result.examples) == (FAIL, (game_id,))


def test_cup_final_without_stats_is_not_required(conn: Connection, baseline: Baseline) -> None:
    """The baseline Cup final has no stats and passes; a missing playoff row does not."""
    sql(conn, "DELETE FROM team_game_stats WHERE game_id = :g", g=PLAYOFF_GAMES[1])
    result = one(conn, "team_rows_per_game")
    assert result.examples == (PLAYOFF_GAMES[1],)
    assert CUP_FINAL not in result.examples


def test_points_not_adding_up_fail_with_the_game_ids(conn: Connection, baseline: Baseline) -> None:
    """Sum off by one, and home/away points swapped with the right sum: both caught."""
    off, swapped = baseline.regular_games[5], baseline.regular_games[3]
    sql(conn, "UPDATE team_game_stats SET pts = pts + 1 WHERE game_id = :g AND is_home", g=off)
    sql(
        conn,
        """
        UPDATE team_game_stats s SET pts = CASE WHEN s.is_home THEN g.away_score
                                               ELSE g.home_score END
        FROM games g WHERE g.game_id = s.game_id AND s.game_id = :g
        """,
        g=swapped,
    )
    assert baseline.games[swapped]["home_score"] != baseline.games[swapped]["away_score"]
    result = one(conn, "points_match_score")
    assert (result.status, result.failed) == (FAIL, 2)
    assert result.examples == tuple(sorted([off, swapped]))


def test_stat_rows_that_do_not_add_up_fail(conn: Connection, baseline: Baseline) -> None:
    """reb != oreb + dreb on one row, and pts != 2*fgm + fg3m + ftm on another."""
    reb_game, pts_game = baseline.regular_games[11], baseline.regular_games[12]
    sql(conn, "UPDATE team_game_stats SET reb = reb + 1 WHERE game_id = :g AND is_home", g=reb_game)
    sql(
        conn,
        "UPDATE team_game_stats SET fg3m = fg3m + 1 WHERE game_id = :g AND NOT is_home",
        g=pts_game,
    )
    home = baseline.tricodes[baseline.games[reb_game]["home_team_id"]]
    away = baseline.tricodes[baseline.games[pts_game]["away_team_id"]]
    result = one(conn, "stat_arithmetic")
    assert (result.status, result.failed) == (FAIL, 2)
    assert result.examples == (
        f"{reb_game}:{home}:leaguegamelog",
        f"{pts_game}:{away}:leaguegamelog",
    )
    assert one(conn, "points_match_score").status == PASS  # pts itself did not change


def test_final_game_without_score_fails(conn: Connection, baseline: Baseline) -> None:
    """The DB CHECKs forbid it, so drop them in the rolled-back transaction to simulate drift."""
    game_id = baseline.regular_games[0]
    sql(conn, "ALTER TABLE games DROP CONSTRAINT ck_games_final_has_score_and_teams")
    sql(conn, "ALTER TABLE games DROP CONSTRAINT ck_games_scores_both_or_none")
    sql(conn, "UPDATE games SET away_score = NULL WHERE game_id = :g", g=game_id)
    result = one(conn, "final_scores_present")
    assert (result.status, result.failed, result.examples) == (FAIL, 1, (game_id,))


def delete_games(conn: Connection, game_ids: list[str]) -> None:
    """Remove games and the rows that point at them."""
    sql(conn, "DELETE FROM odds_events WHERE game_id = ANY(:g)", g=game_ids)
    sql(conn, "DELETE FROM team_game_stats WHERE game_id = ANY(:g)", g=game_ids)
    sql(conn, "DELETE FROM games WHERE game_id = ANY(:g)", g=game_ids)


def unfinish(conn: Connection, game_ids: list[str]) -> None:
    """Turn final games back into scheduled ones (no score, no stats)."""
    sql(conn, "DELETE FROM team_game_stats WHERE game_id = ANY(:g)", g=game_ids)
    sql(
        conn,
        "UPDATE games SET status_code = 1, status_text = '7:30 pm ET', home_score = NULL, "
        "away_score = NULL WHERE game_id = ANY(:g)",
        g=game_ids,
    )


def test_finished_season_short_of_1230_fails(conn: Connection, baseline: Baseline) -> None:
    """1,228 final games fail; a tolerance of 2 (shortened season setting) passes."""
    delete_games(conn, list(baseline.regular_games[-2:]))
    result = one(conn, "regular_season_games")
    assert (result.status, result.examples) == (FAIL, ("2023-24 (1228)",))
    assert "2023-24: 1228 final" in result.note
    relaxed = one(conn, "regular_season_games", settings=CheckSettings(regular_games_tolerance=2))
    assert relaxed.status == PASS


def test_season_in_progress_counts_scheduled_games(conn: Connection, baseline: Baseline) -> None:
    """Unfinished games make the season 'in progress': scheduled games count, no failure."""
    unfinish(conn, list(baseline.regular_games[-15:]))
    result = one(conn, "regular_season_games")
    assert result.status == PASS
    assert "2023-24: in progress, 1230 incl. scheduled (1215 final)" in result.note


def test_season_in_progress_far_off_only_warns(conn: Connection, baseline: Baseline) -> None:
    """In progress and 40 games short (more than the 30 allowed): warn, exit code stays 0."""
    delete_games(conn, list(baseline.regular_games[-40:]))
    unfinish(conn, list(baseline.regular_games[-45:-40]))
    report = run_checks(conn, checks=["regular_season_games"])
    (result,) = report.results
    assert (result.status, result.examples) == (WARN, ("2023-24 (1190)",))
    assert report.exit_code == 0


def test_requested_season_without_games_fails(conn: Connection) -> None:
    """--season for a season with no regular games is a failure, not a silent pass."""
    result = one(conn, "regular_season_games", season="2019-20")
    assert (result.status, result.examples) == (FAIL, ("2019-20 (0)",))


def test_game_count_is_skipped_for_one_date(conn: Connection) -> None:
    """A single date says nothing about season totals."""
    assert one(conn, "regular_season_games", date_et=date(2023, 10, 24)).status == SKIP


def test_team_twice_on_one_eastern_date_fails(conn: Connection, baseline: Baseline) -> None:
    """Moving a day-2 game to 03:00Z on 10-25 = 23:00 ET on 10-24, when both teams also played.

    The UTC dates differ, so this only fails if the check groups by the US Eastern date.
    """
    moved = baseline.games[baseline.regular_games[15]]  # first game of day 2 (10-25 ET)
    start = FIRST_TIP.replace(hour=3, minute=0) + timedelta(days=1)
    sql(
        conn, "UPDATE games SET start_time_utc = :t WHERE game_id = :g", t=start, g=moved["game_id"]
    )
    expected = []
    for team in (moved["home_team_id"], moved["away_team_id"]):
        day_one = next(
            g
            for g in baseline.regular_games[:15]
            if team in (baseline.games[g]["home_team_id"], baseline.games[g]["away_team_id"])
        )
        pair = "/".join(sorted([day_one, moved["game_id"]]))
        expected.append(f"{baseline.tricodes[team]}@2023-10-24:{pair}")
    result = one(conn, "one_game_per_team_day")
    assert (result.status, result.failed) == (FAIL, 2)
    assert result.examples == tuple(sorted(expected))


def test_eastern_date_boundary_does_not_false_alarm(conn: Connection, baseline: Baseline) -> None:
    """A day-2 game moved to 03:00Z on 10-26 is still 23:00 ET on 10-25: no clash.

    Grouping by UTC date would put it on 10-26 with both teams' day-3 games (23:30Z on 10-26).
    """
    moved = baseline.regular_games[15]  # first game of day 2 (10-25 ET)
    start = FIRST_TIP.replace(hour=3, minute=0) + timedelta(days=2)
    sql(conn, "UPDATE games SET start_time_utc = :t WHERE game_id = :g", t=start, g=moved)
    assert one(conn, "one_game_per_team_day").status == PASS


def test_gamelog_score_differs_from_schedule_raw(conn: Connection, baseline: Baseline) -> None:
    """A game-log score that disagrees with the schedule response fails with its ID."""
    game_id = baseline.regular_games[7]
    sql(conn, "UPDATE games SET home_score = home_score + 2 WHERE game_id = :g", g=game_id)
    result = one(conn, "gamelog_vs_schedule_score")
    assert (result.status, result.failed, result.examples) == (FAIL, 1, (game_id,))


def test_gamelog_score_with_home_away_swapped_raw_is_compared_by_team(
    conn: Connection, baseline: Baseline
) -> None:
    """Raw lists the teams the other way round: scores are matched by team ID, so it passes."""
    game_id = baseline.regular_games[8]
    sql(
        conn,
        """
        UPDATE games SET home_team_id = away_team_id, away_team_id = home_team_id,
                         home_score = away_score, away_score = home_score
        WHERE game_id = :g
        """,
        g=game_id,
    )
    sql(
        conn,
        """
        UPDATE team_game_stats s SET is_home = NOT is_home WHERE game_id = :g
        """,
        g=game_id,
    )
    assert one(conn, "gamelog_vs_schedule_score").status == PASS


def test_gamelog_game_without_raw_final_warns(conn: Connection, baseline: Baseline) -> None:
    """One game has no final score in any schedule/scoreboard response: a warning with its ID."""
    sql(
        conn,
        """
        UPDATE raw_api_responses SET response_json = jsonb_set(response_json,
            '{leagueSchedule,gameDates,0,games,0,gameStatus}', '1')
        WHERE endpoint = 'scheduleleaguev2'
        """,
    )
    report = run_checks(conn, checks=["gamelog_vs_schedule_score"])
    (result,) = report.results
    assert (result.status, result.checked, result.passed) == (WARN, 1232, 1231)
    assert result.examples == (baseline.regular_games[0],)
    assert result.note.startswith("1 of 1232 game-log game(s) have no final score")
    assert report.exit_code == 0


def test_gamelog_scores_with_nothing_to_compare_are_skipped(conn: Connection) -> None:
    """No usable raw at all (the daily flow before a final scoreboard): skip, not a silent pass."""
    sql(
        conn,
        "UPDATE raw_api_responses SET response_json = '{}' WHERE endpoint = 'scheduleleaguev2'",
    )
    result = one(conn, "gamelog_vs_schedule_score")
    assert (result.status, result.checked) == (SKIP, 0)
    assert result.note == (
        "none of 1232 game-log game(s) has a final score in a schedule or scoreboard response yet"
    )


def test_scoreboard_raw_newer_than_schedule_is_used(conn: Connection, baseline: Baseline) -> None:
    """The latest raw with the game final wins: a newer scoreboard with another score fails."""
    from .conftest import insert_raw

    game = baseline.games[baseline.regular_games[9]]
    board = {
        "scoreboard": {
            "games": [
                {
                    "gameId": game["game_id"],
                    "gameStatus": 3,
                    "homeTeam": {"teamId": game["home_team_id"], "score": 1},
                    "awayTeam": {"teamId": game["away_team_id"], "score": 2},
                }
            ]
        }
    }
    raw_id = insert_raw(conn, "nba_api", "scoreboardv3", {"GameDate": "2023-10-24"}, board)
    sql(
        conn,
        "UPDATE raw_api_responses SET last_fetched_at = last_fetched_at + interval '1 day' "
        "WHERE raw_response_id = :r",
        r=raw_id,
    )
    result = one(conn, "gamelog_vs_schedule_score")
    assert result.examples == (game["game_id"],)


def test_box_score_as_representative_outside_cup_final_fails(
    conn: Connection, baseline: Baseline
) -> None:
    """A regular game whose only stats are a box score fails; a Cup final box score is fine."""
    game = baseline.games[baseline.regular_games[30]]
    sql(conn, "DELETE FROM team_game_stats WHERE game_id = :g", g=game["game_id"])
    raw = conn.execute(text("SELECT min(raw_response_id) FROM raw_api_responses")).scalar_one()
    insert_stats(conn, game, raw, source="boxscoretraditionalv3")
    insert_stats(conn, baseline.games[CUP_FINAL], raw, source="boxscoretraditionalv3")
    result = one(conn, "primary_stats_source")
    assert (result.status, result.examples) == (FAIL, (game["game_id"],))
    # The game log row, once loaded, is the representative again.
    insert_stats(conn, game, raw)
    assert one(conn, "primary_stats_source").status == PASS


def test_missing_alias_and_extra_team_fail(conn: Connection, baseline: Baseline) -> None:
    """A team without an odds alias, and a 31st team without any alias."""
    den = next(t for t, code in baseline.tricodes.items() if code == "DEN")
    sql(conn, "DELETE FROM team_aliases WHERE source = 'the_odds_api' AND team_id = :t", t=den)
    sql(
        conn,
        "INSERT INTO teams (team_id, tricode, city, nickname, full_name, source, fetched_at) "
        "VALUES (1, 'XXX', 'X', 'X', 'X X', 'test', now())",
    )
    result = one(conn, "teams_and_aliases")
    assert result.status == FAIL
    assert result.examples == (
        "teams=31",
        "DEN:no_the_odds_api_alias",
        "XXX:no_nba_api_alias",
        "XXX:no_the_odds_api_alias",
    )


def test_snapshot_after_start_fails(conn: Connection, baseline: Baseline) -> None:
    """The CHECK forbids it, so drop it in the rolled-back transaction to simulate drift."""
    from .conftest import insert_snapshot

    event = first_odds_event(conn)
    start = baseline.games[baseline.odds_game]["start_time_utc"]
    raw = conn.execute(text("SELECT max(raw_response_id) FROM raw_api_responses")).scalar_one()
    sql(
        conn,
        "ALTER TABLE odds_snapshots DROP CONSTRAINT ck_odds_snapshots_captured_before_commence",
    )
    bad = insert_snapshot(conn, event, start, timedelta(minutes=5), raw)
    result = one(conn, "odds_after_start")
    assert (result.status, result.examples) == (FAIL, (str(bad),))


def test_snapshot_after_the_events_moved_start_warns(conn: Connection, baseline: Baseline) -> None:
    """The event moves 3 h earlier: the snapshot taken 2 h before the old start is now 1 h late.

    Its own commence_time still passes the DB CHECK, and the closing view leaves it out, so this
    is a warning (a late capture to look at), not a failure.
    """
    event = first_odds_event(conn)
    snapshot = conn.execute(
        text("SELECT odds_snapshot_id FROM odds_snapshots WHERE odds_event_id = :e"), {"e": event}
    ).scalar_one()
    assert one(conn, "odds_after_start").status == PASS
    sql(
        conn,
        "UPDATE odds_events SET commence_time = commence_time - interval '3 hours' "
        "WHERE odds_event_id = :e",
        e=event,
    )
    report = run_checks(conn, checks=["odds_after_start"])
    (result,) = report.results
    assert (result.status, result.examples) == (WARN, (f"event_start:{snapshot}",))
    assert report.exit_code == 0


def test_closing_line_view_change_is_caught(conn: Connection, baseline: Baseline) -> None:
    """If odds_closing_lines lost its 'before the start' rule, the post-start row shows up."""
    from .conftest import insert_snapshot

    event = first_odds_event(conn)
    raw = conn.execute(text("SELECT max(raw_response_id) FROM raw_api_responses")).scalar_one()
    # The snapshot is valid (before its own commence_time) but the event later moved earlier.
    late = insert_snapshot(
        conn,
        event,
        baseline.games[baseline.odds_game]["start_time_utc"] + timedelta(minutes=30),
        timedelta(minutes=-5),
        raw,
    )
    # The real view excludes it; it is only a warning for being late against the event start.
    before = one(conn, "odds_after_start")
    assert (before.status, before.examples) == (WARN, (f"event_start:{late}",))
    sql(
        conn,
        """
        CREATE OR REPLACE VIEW odds_closing_lines AS
        SELECT DISTINCT ON (s.odds_event_id, s.market_key, s.bookmaker_key, s.outcome_side)
               s.*, e.commence_time AS event_commence_time, e.game_id,
               e.commence_time - s.captured_at AS lead_time
        FROM odds_snapshots s JOIN odds_events e USING (odds_event_id)
        ORDER BY s.odds_event_id, s.market_key, s.bookmaker_key, s.outcome_side,
                 s.captured_at DESC
        """,
    )
    result = one(conn, "odds_after_start")
    assert (result.status, result.examples) == (FAIL, (f"closing:{late}", f"event_start:{late}"))


def insert_event(conn: Connection, event: str, game_id: str | None, start: Any) -> int:
    """An odds event (unmatched when game_id is None)."""
    raw = conn.execute(text("SELECT max(raw_response_id) FROM raw_api_responses")).scalar_one()
    return conn.execute(
        text(
            """
            INSERT INTO odds_events (source, source_event_id, home_team_name, away_team_name,
                commence_time, game_id, match_method, matched_at, fetched_at, raw_response_id)
            VALUES ('the_odds_api', :event, 'Home', 'Away', :start, :game_id,
                CASE WHEN :matched THEN 'manual' END, CASE WHEN :matched THEN now() END,
                now(), :raw)
            RETURNING odds_event_id
            """
        ),
        {
            "event": event,
            "start": start,
            "game_id": game_id,
            "matched": game_id is not None,
            "raw": raw,
        },
    ).scalar_one()


def test_unmatched_odds_events_warn_with_reasons(conn: Connection, baseline: Baseline) -> None:
    """Two unmatched events: one with the reason the loader recorded, one without."""
    start = baseline.games[baseline.odds_game]["start_time_utc"]
    insert_event(conn, "evt-alias", None, start)
    insert_event(conn, "evt-none", None, start + timedelta(hours=1))
    sql(
        conn,
        """
        INSERT INTO ingestion_runs (job_type, source, scope_key, status, finished_at, error_detail)
        VALUES ('odds_events', 'the_odds_api', 'the_odds_api', 'succeeded', now(),
            CAST(:detail AS jsonb))
        """,
        detail='{"quality_issues": [{"kind": "unknown_team_alias", "key": "evt-alias", '
        '"message": "no team_aliases"}, {"kind": "bad_price", "key": "evt-none", "message": ""}]}',
    )
    report = run_checks(conn, checks=["odds_unmatched_events"])
    (result,) = report.results
    assert (result.status, result.checked, result.examples) == (WARN, 3, ("evt-alias", "evt-none"))
    assert result.note == "2/3 unmatched (66.7%); no_recorded_reason 1, unknown_team_alias 1"
    assert report.exit_code == 0


def test_two_odds_events_on_one_game_warn(conn: Connection, baseline: Baseline) -> None:
    """A second event mapped to the same game (e.g. re-created after a postponement)."""
    start = baseline.games[baseline.odds_game]["start_time_utc"]
    insert_event(conn, "evt-dup", baseline.odds_game, start)
    result = one(conn, "odds_events_per_game")
    assert (result.status, result.examples) == (WARN, (baseline.odds_game,))


def test_past_game_not_final_warns(conn: Connection, baseline: Baseline) -> None:
    """A 2023 game still scheduled is stale (its start is long before the DB clock)."""
    game_id = baseline.regular_games[50]
    unfinish(conn, [game_id])
    result = one(conn, "past_games_not_final")
    assert (result.status, result.examples) == (WARN, (game_id,))


def test_failed_run_warns_until_redone(conn: Connection) -> None:
    """A partial run is listed; a later succeeded run of the same unit clears it."""
    run_id = conn.execute(
        text(
            """
            INSERT INTO ingestion_runs (job_type, source, scope_key, status, started_at,
                finished_at)
            VALUES ('gamelog', 'nba_api', 'season=2023-24;season_type=Playoffs', 'partial',
                now() - interval '1 hour', now())
            RETURNING run_id
            """
        )
    ).scalar_one()
    result = one(conn, "recent_failed_runs")
    assert (result.status, result.examples) == (WARN, (f"{run_id}:gamelog:partial",))
    sql(
        conn,
        """
        INSERT INTO ingestion_runs (job_type, source, scope_key, status, finished_at)
        VALUES ('gamelog', 'nba_api', 'season=2023-24;season_type=Playoffs', 'succeeded', now())
        """,
    )
    redone = one(conn, "recent_failed_runs")
    assert redone.status == PASS
    assert redone.note.startswith("0 not redone, 1 redone")


def test_old_failed_run_is_outside_the_window(conn: Connection) -> None:
    """A failed run from 30 days ago is outside the default 7-day window."""
    sql(
        conn,
        """
        INSERT INTO ingestion_runs (job_type, source, scope_key, status, started_at, finished_at)
        VALUES ('schedule', 'nba_api', 'season=2024-25', 'failed',
            now() - interval '30 days', now() - interval '30 days')
        """,
    )
    assert one(conn, "recent_failed_runs").status == PASS
    wide = one(conn, "recent_failed_runs", settings=CheckSettings(recent_run_days=31))
    assert wide.status == WARN


def test_date_scope_limits_games_to_one_eastern_date(conn: Connection) -> None:
    """--date: the first game day only (15 games); season scope with no data checks nothing."""
    day = run_checks(conn, date_et=date(2023, 10, 24))
    by_name = {r.name: r for r in day.results}
    assert by_name["team_rows_per_game"].checked == 15
    assert by_name["one_game_per_team_day"].checked == 30
    assert by_name["odds_after_start"].checked == 0
    assert "ET 2023-10-24" in by_name["team_rows_per_game"].target
    other = run_checks(conn, season="2024-25", checks=["team_rows_per_game"])
    assert other.results[0].checked == 0
    assert other.results[0].status == PASS


def test_last_day_and_season_scope_combined(conn: Connection, baseline: Baseline) -> None:
    """Season and date together narrow to the intersection."""
    last = (FIRST_TIP + timedelta(days=DAYS - 1)).date()
    report = run_checks(conn, season="2023-24", date_et=last, checks=["team_rows_per_game"])
    assert report.results[0].checked == 15
    assert report.scope.describe() == f"season 2023-24, ET {last.isoformat()}"


def test_check_error_is_reported_and_others_still_run(conn: Connection) -> None:
    """A column the odds checks need disappears: those checks error, the rest pass, exit 1."""
    sql(conn, "ALTER TABLE odds_events RENAME COLUMN game_id TO mapped_game")
    report = run_checks(conn)
    status = {r.name: r.status for r in report.results}
    errored = {name for name, s in status.items() if s == ERROR}
    # odds_after_start reads the view, which follows a renamed column by its number.
    assert errored == {"odds_unmatched_events", "odds_events_per_game"}
    others = {name: s for name, s in status.items() if name not in errored}
    assert others == dict.fromkeys(others, PASS) | NOTHING_TO_COMPARE
    assert report.exit_code == 1
    note = next(r.note for r in report.results if r.name == "odds_events_per_game")
    assert note.startswith("check could not run: ProgrammingError")


def test_checks_run_read_only(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    """With an Engine the transaction is READ ONLY: a check that writes errors out."""

    def writes(conn: Connection, scope: Any, cfg: Any) -> Outcome:
        """A misbehaving check."""
        conn.execute(text("DELETE FROM ingestion_runs"))
        return Outcome(0)

    bad = checks.Check("writer", "x", "tries to write", writes)
    monkeypatch.setattr(checks, "CHECKS", (*checks.CHECKS, bad))
    monkeypatch.setattr(checks, "CHECK_NAMES", (*checks.CHECK_NAMES, "writer"))
    report = run_checks(engine, checks=["teams_and_aliases", "writer"])
    assert [r.status for r in report.results] == [PASS, ERROR]
    assert "read-only transaction" in report.results[1].note
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM ingestion_runs")).scalar_one() == 1


def test_engine_run_matches_connection_run(engine: Engine, conn: Connection) -> None:
    """The public entry point for P-017 (Engine) gives the same report as a connection."""
    assert run_checks(engine).as_dict() == run_checks(conn).as_dict()


def test_unknown_check_name_is_rejected(conn: Connection) -> None:
    """A typo in a check name raises instead of silently checking nothing."""
    with pytest.raises(ValueError, match="unknown check"):
        run_checks(conn, checks=["team_rows"])


def test_report_exit_code_and_dict() -> None:
    """fail/error -> 1; pass/warn/skip -> 0; as_dict carries what --json prints."""

    def row(status: str) -> CheckResult:
        """A minimal result."""
        return CheckResult("c", "t", status, 3, 1, ("x",), "d")

    scope = checks.Scope("2023-24")
    assert QualityReport(scope, (row(PASS), row(WARN), row(SKIP))).exit_code == 0
    assert QualityReport(scope, (row(PASS), row(FAIL))).exit_code == 1
    assert QualityReport(scope, (row(ERROR),)).exit_code == 1
    data = QualityReport(scope, (row(FAIL),)).as_dict()
    assert data["scope"] == {"season": "2023-24", "date_et": None}
    assert data["summary"] == {"pass": 0, "fail": 1, "warn": 0, "error": 0, "skip": 0}
    assert data["checks"][0]["passed"] == 2


# Modeling layer checks (P-031, schema v1 9-1 5): rows whose as_of is not before tip-off.
def insert_feature(conn: Connection, game_id: str, as_of: Any, values: dict[str, Any]) -> int:
    """Insert one backtest features row (fv1, daily) and return its feature_id."""
    return conn.execute(
        text(
            """
            INSERT INTO features (game_id, as_of, feature_version, snapshot_kind, origin,
                "values", input_fingerprint, computed_at, code_version)
            VALUES (:g, :as_of, 'fv1', 'daily', 'backtest', CAST(:v AS jsonb), '{}', :as_of,
                'test')
            RETURNING feature_id
            """
        ),
        {"g": game_id, "as_of": as_of, "v": json.dumps(values)},
    ).scalar_one()


def insert_prediction(conn: Connection, game_id: str, as_of: Any, start: Any) -> int:
    """Insert one model configuration and a backtest prediction; return the prediction_id."""
    model_id = conn.execute(
        text(
            """
            INSERT INTO model_versions (model_name, version, model_family, hyperparameters,
                seed, code_version, training_window)
            VALUES ('b0', '1', 'baseline_home', '{}', 0, 'test', '{}')
            RETURNING model_version_id
            """
        )
    ).scalar_one()
    return conn.execute(
        text(
            """
            INSERT INTO predictions (game_id, model_version_id, as_of, snapshot_kind, origin,
                computed_at, train_cutoff, scheduled_start_utc, p_home, market_status,
                code_version)
            VALUES (:g, :m, :as_of, 'daily', 'backtest', :as_of, :as_of, :start, 0.55,
                'no_odds_season', 'test')
            RETURNING prediction_id
            """
        ),
        {"g": game_id, "m": model_id, "as_of": as_of, "start": start},
    ).scalar_one()


def test_feature_at_tipoff_fails_and_before_passes(conn: Connection, baseline: Baseline) -> None:
    """as_of = start_time_utc is too late (fail, with the row); one second earlier is fine."""
    early, late = baseline.regular_games[0], baseline.regular_games[1]
    insert_feature(conn, early, baseline.games[early]["start_time_utc"] - timedelta(seconds=1), {})
    bad = insert_feature(conn, late, baseline.games[late]["start_time_utc"], {})
    result = one(conn, "features_after_tipoff")
    assert (result.status, result.checked, result.failed) == (FAIL, 2, 1)
    assert result.examples == (f"{bad}:{late}",)
    assert one(conn, "features_after_tipoff", season="2024-25").checked == 0


def test_prediction_after_game_moved_earlier_fails(conn: Connection, baseline: Baseline) -> None:
    """A prediction made before the scheduled start fails once the game moves before its as_of."""
    game = baseline.regular_games[0]
    start = baseline.games[game]["start_time_utc"]
    as_of = start - timedelta(hours=12)
    prediction = insert_prediction(conn, game, as_of, start)
    assert one(conn, "predictions_after_tipoff").status == PASS
    sql(conn, "UPDATE games SET start_time_utc = :t WHERE game_id = :g", t=as_of, g=game)
    result = one(conn, "predictions_after_tipoff")
    assert (result.status, result.failed, result.examples) == (FAIL, 1, (f"{prediction}:{game}",))


def test_feature_drift_reports_a_recomputed_difference(
    conn: Connection, baseline: Baseline
) -> None:
    """T11: a recomputation that differs is refused by DO NOTHING and reported as a warning."""
    game = baseline.regular_games[0]
    as_of = baseline.games[game]["start_time_utc"] - timedelta(hours=12)
    feature_id = insert_feature(conn, game, as_of, {"elo_home": 1510.0, "n": None})
    rows = conn.execute(
        text(
            """
            INSERT INTO features (game_id, as_of, feature_version, snapshot_kind, origin,
                "values", input_fingerprint, computed_at, code_version)
            VALUES (:g, :as_of, 'fv1', 'daily', 'backtest', '{"elo_home": 1600.0}', '{}',
                :as_of, 'test')
            ON CONFLICT DO NOTHING
            """
        ),
        {"g": game, "as_of": as_of},
    ).rowcount
    assert rows == 0
    seen: list[Any] = []

    def recompute(_: Connection, row: checks.StoredFeature) -> dict[str, Any]:
        seen.append(row)
        return {"elo_home": 1600.0, "n": None}

    result = one(conn, "feature_drift", settings=CheckSettings(feature_recompute=recompute))
    assert (result.status, result.checked, result.failed) == (WARN, 1, 1)
    assert result.examples == (f"{feature_id}:{game}:fv1:elo_home",)
    assert seen[0].values == {"elo_home": 1510.0, "n": None}  # the stored row is unchanged
    assert (seen[0].as_of, seen[0].feature_version, seen[0].origin) == (as_of, "fv1", "backtest")
    same = CheckSettings(feature_recompute=lambda _, row: dict(row.values))
    assert one(conn, "feature_drift", settings=same).status == PASS
    unavailable = one(
        conn, "feature_drift", settings=CheckSettings(feature_recompute=lambda *_: None)
    )
    assert (unavailable.status, unavailable.checked) == (PASS, 0)
    assert unavailable.note == "1 row(s) could not be recomputed"


def test_feature_drift_skips_without_anything_to_compare(conn: Connection) -> None:
    """No recompute function, or no stored rows: skip (not pass, not fail)."""
    result = one(conn, "feature_drift")
    assert (result.status, result.note) == (SKIP, "no recompute function (feature module, P-035)")
    settings = CheckSettings(feature_recompute=lambda _, row: dict(row.values))
    result = one(conn, "feature_drift", settings=settings)
    assert (result.status, result.note) == (SKIP, "no stored features rows to compare")


def test_modeling_checks_skip_before_migration_0002(conn: Connection) -> None:
    """A database still at 0001 (e.g. before the PM applies 0002) skips, it does not error."""
    sql(conn, "DROP TABLE evaluations, predictions, features, model_versions CASCADE")
    settings = CheckSettings(feature_recompute=lambda _, row: dict(row.values))
    report = run_checks(conn, settings=settings)
    status = {r.name: (r.status, r.note) for r in report.results}
    missing = "table {} does not exist (migration 0002)"
    assert status["features_after_tipoff"] == (SKIP, missing.format("features"))
    assert status["predictions_after_tipoff"] == (SKIP, missing.format("predictions"))
    assert status["feature_drift"] == (SKIP, missing.format("features"))
    assert report.exit_code == 0
