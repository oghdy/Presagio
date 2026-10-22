"""presagio backfill: plan, dry-run, resume, failure list and block handling (P-016 1-4).

Runs go CLI -> backfill -> loaders -> adapters -> IngestionClient(MockTransport serving
tests/fixtures/nba_api) -> temp database (D-030). No network. No test compares Python time
with the database clock: "season over" uses an injected now, and run rows are compared only
with each other.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, func, insert, select, text

import presagio.db.models  # noqa: F401  (registers every table on Base.metadata)
from presagio.cli import main
from presagio.db.base import Base
from presagio.db.models import IngestionRun
from presagio.ingestion.backfill import (
    RUN,
    SKIP,
    BackfillRequest,
    BackfillSources,
    Unit,
    build_units,
    estimate,
    et_day_bounds,
    parse_season,
    plan_backfill,
    season_of_date,
    seasons_for_range,
)
from presagio.ingestion.client import IngestionClient, db_raw_store
from presagio.ingestion.runs import current_code_version

from .helpers import fixture_bytes, fixture_json, fixture_name, html, json_bytes, keep_log_games

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)  # after both 2024-25 and 2025-26 were over
SEASON = "2024-25"
TYPES = ["--season-type", "regular", "--season-type", "playoffs"]  # fixtures have no 2024-25 PlayIn
LOG_REGULAR = "leaguegamelog__2024-25_RegularSeason"
LOG_PLAYOFFS = "leaguegamelog__2024-25_Playoffs"
# Game log fixture games that are also in the trimmed 2024-25 schedule fixture; the other
# game of each log is not, which makes a plain fixture game log run 'partial' (D-034).
IN_SCHEDULE_2024 = ("0022400061", "0042400121")


@dataclass
class Stub:
    """Fixture-serving nba_api sources; overrides replace bodies, blocked answer HTML errors."""

    sources: BackfillSources
    seen: list[str]
    overrides: dict[str, bytes] = field(default_factory=dict)
    blocked: dict[str, int] = field(default_factory=dict)

    def factory(self) -> Callable[[Engine], BackfillSources]:
        """What main(sources=...) expects."""
        return lambda engine: self.sources

    def clean_game_logs(self) -> None:
        """Serve 2024-25 game logs with only games the schedule has, so the units succeed."""
        for name in (LOG_REGULAR, LOG_PLAYOFFS):
            data = keep_log_games(fixture_json(name), *IN_SCHEDULE_2024)
            self.overrides[name] = json.dumps(data).encode()


@pytest.fixture
def db(engine: Engine) -> Engine:
    """Temp DB with teams emptied too (engine already empties raw, runs and what cascades)."""
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE teams, team_aliases CASCADE"))
    return engine


@pytest.fixture
def nba(db: Engine, make_client: Callable[..., IngestionClient]) -> Stub:
    """Sources over one client whose HTTP goes to fixtures and whose raw goes to the temp DB."""
    seen: list[str] = []
    overrides: dict[str, bytes] = {}
    blocked: dict[str, int] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        """Serve fixtures by request; blocked names get an HTML error page."""
        name = fixture_name(request)
        seen.append(name)
        if name in blocked:
            return html(blocked[name])
        return json_bytes(200, overrides[name] if name in overrides else fixture_bytes(name))

    client = make_client(handler, store=db_raw_store(db))
    return Stub(BackfillSources.nba_api(client), seen, overrides, blocked)


def backfill(db: Engine, nba: Stub, *args: str) -> int:
    """Run `presagio backfill <args>` in-process against the temp DB and the stub."""
    return main(["backfill", *args], engine=db, sources=nba.factory(), now=NOW, setup_logging=False)


def table_counts(db: Engine) -> dict[str, int]:
    """Row count of every table in the schema."""
    with db.connect() as conn:
        return {
            t.name: conn.execute(select(func.count()).select_from(t)).scalar_one()
            for t in Base.metadata.sorted_tables
        }


def runs(db: Engine) -> list[Any]:
    """Every ingestion_runs row, oldest first."""
    with db.connect() as conn:
        return conn.execute(select(IngestionRun.__table__).order_by("run_id")).all()


def scalar(db: Engine, sql: str) -> Any:
    """One value from a query."""
    with db.connect() as conn:
        return conn.execute(text(sql)).scalar_one()


# --- plan: seasons, date ranges, units (no database) -----------------------------------------


def test_season_format_is_checked() -> None:
    """'2023-24' is a season; a wrong second year or format is not."""
    assert parse_season("2023-24") == "2023-24"
    assert parse_season("1999-00") == "1999-00"
    for bad in ("2023-25", "2023", "23-24", "2023/24"):
        with pytest.raises(ValueError, match="not a season"):
            parse_season(bad)


def test_date_range_is_widened_to_whole_seasons() -> None:
    """A range maps to every season its dates touch (window starts Oct 15)."""
    assert seasons_for_range(date(2024, 1, 1), date(2024, 1, 31)) == ["2023-24"]
    assert seasons_for_range(date(2024, 6, 1), date(2024, 11, 1)) == ["2023-24", "2024-25"]
    assert season_of_date(date(2020, 10, 11)) == "2019-20"  # bubble finals, game 6
    assert season_of_date(date(2024, 10, 22)) == "2024-25"  # opening night
    with pytest.raises(ValueError, match="before"):
        seasons_for_range(date(2024, 2, 1), date(2024, 1, 1))


def test_et_dates_become_a_utc_interval() -> None:
    """ET date range -> [start 00:00 ET, end+1 00:00 ET) in UTC (schema rule 1-2.4)."""
    lower, upper = et_day_bounds(date(2025, 1, 1), date(2025, 1, 31))
    assert lower == datetime(2025, 1, 1, 5, 0, tzinfo=UTC)  # EST = UTC-5
    assert upper == datetime(2025, 2, 1, 5, 0, tzinfo=UTC)


def test_units_follow_fk_order_and_loader_scope_keys() -> None:
    """teams first, then per season: schedule, then one game log per season type."""
    request = BackfillRequest.for_seasons(
        ["2025-26", "2024-25", "2025-26"], ["playoffs", "regular"]
    )
    assert request.seasons == ("2024-25", "2025-26")
    assert request.season_types == ("regular", "playoffs")
    assert [(u.job_type, u.scope_key) for u in build_units(request)] == [
        ("teams", "nba_api"),
        ("schedule", "season=2024-25"),
        ("gamelog", "season=2024-25;season_type=Regular Season"),
        ("gamelog", "season=2024-25;season_type=Playoffs"),
        ("schedule", "season=2025-26"),
        ("gamelog", "season=2025-26;season_type=Regular Season"),
        ("gamelog", "season=2025-26;season_type=Playoffs"),
    ]
    assert request.scope_key() == "seasons=2024-25,2025-26;season_types=regular,playoffs"
    ranged = BackfillRequest.for_range(date(2024, 1, 1), date(2024, 1, 31))
    assert ranged.scope_key() == (
        "from=2024-01-01;to=2024-01-31;seasons=2023-24;season_types=regular,playin,playoffs"
    )


def test_estimate_is_calls_times_interval_plus_response_time() -> None:
    """Only units that run count; schedule assumes 2.5 s, game log 1.5 s, teams 0.5 s."""
    from presagio.ingestion.backfill import PlannedUnit

    plan = [
        PlannedUnit(Unit("teams", "nba_api"), SKIP, "done"),
        PlannedUnit(Unit("schedule", "season=2024-25", "2024-25"), RUN, "new"),
        PlannedUnit(Unit("gamelog", "season=2024-25;season_type=Playoffs", "2024-25"), RUN, "new"),
    ]
    est = estimate(plan, min_interval=1.0)
    assert est.calls == 2
    assert est.per_unit == (0.0, 3.5, 2.5)
    assert est.seconds == 6.0


# --- criterion 2: dry run --------------------------------------------------------------------


def test_dry_run_prints_calls_and_time_and_writes_nothing(db: Engine, nba: Stub, capsys) -> None:
    """Every table keeps its row count, no HTTP request is made, the plan is printed."""
    nba.clean_game_logs()
    assert backfill(db, nba, "--season", SEASON, *TYPES) == 0  # some history to plan against
    capsys.readouterr()
    before, requests = table_counts(db), len(nba.seen)
    assert backfill(db, nba, "--season", "2025-26", "--season", SEASON, "--dry-run") == 0
    out = capsys.readouterr().out
    assert table_counts(db) == before
    assert len(nba.seen) == requests
    assert "dry run: nothing is written" in out
    # 2025-26 schedule (3.5 s) + its 3 game logs + the never-run 2024-25 PlayIn (2.5 s each)
    assert "Would make: 5 call(s), about 14 s" in out
    assert "season=2024-25;season_type=PlayIn" in out  # never run -> run
    assert out.count(" skip ") == 4  # teams + 2024-25 schedule and its two game logs


def test_dry_run_never_builds_the_sources(db: Engine, capsys) -> None:
    """--dry-run does not even create an HTTP client."""

    def no_sources(engine: Engine) -> BackfillSources:
        """Fail if called."""
        raise AssertionError("dry run built the sources")

    code = main(
        ["backfill", "--from", "2025-01-01", "--to", "2025-01-31", "--dry-run"],
        engine=db,
        sources=no_sources,
        now=NOW,
        setup_logging=False,
    )
    assert code == 0
    assert "Would make: 5 call(s)" in capsys.readouterr().out
    assert table_counts(db)["ingestion_runs"] == 0


# --- criteria 1 and 3: seasons, ranges, progress in ingestion_runs, resume -------------------


def test_season_backfill_records_parent_and_unit_runs(db: Engine, nba: Stub, capsys) -> None:
    """One parent 'backfill' row, one child per unit with the planned scope_key, code_version."""
    nba.clean_game_logs()
    assert backfill(db, nba, "--season", SEASON, *TYPES) == 0
    rows = runs(db)
    parent, children = rows[0], rows[1:]
    assert (parent.job_type, parent.status, parent.parent_run_id) == ("backfill", "succeeded", None)
    assert parent.scope_key == "seasons=2024-25;season_types=regular,playoffs"
    assert parent.items_total == 4 and parent.items_succeeded == 4
    assert parent.error_detail["totals"]["api_calls"] == 4
    assert [(c.job_type, c.scope_key, c.status) for c in children] == [
        ("teams", "nba_api", "succeeded"),
        ("schedule", "season=2024-25", "succeeded"),
        ("gamelog", "season=2024-25;season_type=Regular Season", "succeeded"),
        ("gamelog", "season=2024-25;season_type=Playoffs", "succeeded"),
    ]
    assert {c.parent_run_id for c in children} == {parent.run_id}
    assert {r.code_version for r in rows} == {current_code_version()}
    assert nba.seen == [
        "commonteamyears__00",
        "scheduleleaguev2__2024-25",
        LOG_REGULAR,
        LOG_PLAYOFFS,
    ]
    assert scalar(db, "SELECT count(*) FROM team_game_stats") == 4  # 2 games x 2 teams
    out = capsys.readouterr().out
    assert f"Parent run {parent.run_id}: succeeded; 4 call(s)" in out
    assert "Failures" not in out and "Exit code 0" in out


def test_rerun_skips_done_units_and_force_reloads_idempotently(db: Engine, nba: Stub) -> None:
    """Second run: 0 calls, all skipped. --force: every call again, same row counts."""
    nba.clean_game_logs()
    assert backfill(db, nba, "--season", SEASON, *TYPES) == 0
    counts = table_counts(db)
    assert backfill(db, nba, "--season", SEASON, *TYPES) == 0
    assert len(nba.seen) == 4  # nothing new requested
    second_parent = runs(db)[-1]
    assert second_parent.job_type == "backfill" and second_parent.items_succeeded == 4
    assert backfill(db, nba, "--season", SEASON, *TYPES, "--force") == 0
    assert len(nba.seen) == 8
    after = table_counts(db)
    data_tables = ("teams", "team_aliases", "games", "team_game_stats", "raw_api_responses")
    assert {t: after[t] for t in data_tables} == {t: counts[t] for t in data_tables}
    # Run log grows (it is a journal): 5 rows, then a parent alone, then 5 rows again.
    assert after["ingestion_runs"] == counts["ingestion_runs"] + 1 + 5


def test_partial_units_run_again_on_resume(db: Engine, nba: Stub, capsys) -> None:
    """Plain fixtures leave each game log 'partial'; the rerun calls only those two again."""
    assert backfill(db, nba, "--season", SEASON, *TYPES) == 1
    out = capsys.readouterr().out
    assert "Failures (2):" in out
    assert "- gamelog season=2024-25;season_type=Playoffs: partial:" in out
    nba.clean_game_logs()
    assert backfill(db, nba, "--season", SEASON, *TYPES) == 0
    assert nba.seen[4:] == [LOG_REGULAR, LOG_PLAYOFFS]


def test_season_not_over_is_never_skipped(db: Engine, nba: Stub) -> None:
    """While a season is still going, a succeeded unit runs again (its data still changes)."""
    nba.clean_game_logs()
    assert backfill(db, nba, "--season", SEASON, *TYPES) == 0
    request = BackfillRequest.for_seasons([SEASON], ["regular", "playoffs"])
    during = plan_backfill(db, request, now=datetime(2025, 3, 1, tzinfo=UTC))
    assert [(p.unit.job_type, p.action) for p in during] == [
        ("teams", SKIP),
        ("schedule", RUN),
        ("gamelog", RUN),
        ("gamelog", RUN),
    ]
    assert during[1].reason == "season not over; last succeeded (run 3)"
    after = plan_backfill(db, request, now=NOW)
    assert {p.action for p in after} == {SKIP}


def test_success_before_the_season_ended_does_not_count_as_done(db: Engine, nba: Stub) -> None:
    """A unit that succeeded while its season was still going is loaded again once it is over."""
    nba.clean_game_logs()
    assert backfill(db, nba, "--season", SEASON, *TYPES) == 0
    with db.begin() as conn:  # pretend the schedule run happened in March 2025
        conn.execute(
            text(
                "UPDATE ingestion_runs SET started_at = '2025-03-01T00:00:00Z', "
                "finished_at = '2025-03-01T00:01:00Z' WHERE job_type = 'schedule'"
            )
        )
    plan = plan_backfill(db, BackfillRequest.for_seasons([SEASON], ["regular"]), now=NOW)
    assert [(p.unit.job_type, p.action) for p in plan] == [
        ("teams", SKIP),
        ("schedule", RUN),
        ("gamelog", SKIP),
    ]


def test_date_range_loads_the_seasons_it_touches(db: Engine, nba: Stub, capsys) -> None:
    """--from/--to: the season's units run under the season scope keys; the range is recorded."""
    nba.clean_game_logs()
    code = backfill(db, nba, "--from", "2024-10-22", "--to", "2024-10-31", *TYPES)
    assert code == 0
    parent = runs(db)[0]
    assert parent.scope_key == (
        "from=2024-10-22;to=2024-10-31;seasons=2024-25;season_types=regular,playoffs"
    )
    assert nba.seen == [
        "commonteamyears__00",
        "scheduleleaguev2__2024-25",
        LOG_REGULAR,
        LOG_PLAYOFFS,
    ]
    # The trimmed fixture has one game in the range: the 2024-10-22 opener, 23:30 UTC (19:30 ET).
    assert "Games in 2024-10-22..2024-10-31 (ET): 1, final 1" in capsys.readouterr().out
    # A later season run finds the same units done.
    assert backfill(db, nba, "--season", SEASON, *TYPES) == 0
    assert len(nba.seen) == 4


# --- criterion 4: failures do not stop the backfill; blocks do --------------------------------


def test_failed_unit_is_listed_and_the_rest_still_runs(db: Engine, nba: Stub, capsys) -> None:
    """A game log that is not JSON fails; the next game log still runs; exit code 1."""
    nba.clean_game_logs()
    nba.overrides[LOG_REGULAR] = b"<html>maintenance</html>"
    assert backfill(db, nba, "--season", SEASON, *TYPES) == 1
    assert nba.seen[-2:] == [LOG_REGULAR, LOG_PLAYOFFS]
    statuses = [(r.job_type, r.status) for r in runs(db)]
    assert statuses == [
        ("backfill", "partial"),
        ("teams", "succeeded"),
        ("schedule", "succeeded"),
        ("gamelog", "failed"),
        ("gamelog", "succeeded"),
    ]
    out = capsys.readouterr().out
    assert "Failures (1):" in out
    assert (
        "- gamelog season=2024-25;season_type=Regular Season: failed: InvalidResponseError" in out
    )
    assert "Exit code 1" in out


def test_block_stops_the_whole_backfill_at_once(db: Engine, nba: Stub, capsys) -> None:
    """403 on the schedule: unit and parent 'aborted', nothing after it is called, exit 2."""
    nba.blocked["scheduleleaguev2__2024-25"] = 403
    assert backfill(db, nba, "--season", SEASON, "--season", "2025-26", *TYPES) == 2
    assert nba.seen == ["commonteamyears__00", "scheduleleaguev2__2024-25"]
    statuses = [(r.job_type, r.status) for r in runs(db)]
    assert statuses == [("backfill", "aborted"), ("teams", "succeeded"), ("schedule", "aborted")]
    assert runs(db)[0].error_message.startswith("BlockedError: ")
    out = capsys.readouterr().out
    assert "Not reached after the block (5):" in out
    assert "Stopped: the source blocked us (403/429)" in out and "Exit code 2" in out


def test_failed_schedule_without_games_skips_its_game_logs(db: Engine, nba: Stub, capsys) -> None:
    """No games for the season -> its game logs are not called (they could only fail)."""
    nba.overrides["scheduleleaguev2__2024-25"] = b"not json"
    assert backfill(db, nba, "--season", SEASON, *TYPES) == 1
    assert nba.seen == ["commonteamyears__00", "scheduleleaguev2__2024-25"]
    out = capsys.readouterr().out
    assert "Failures (3):" in out
    assert "not_run: schedule 2024-25 failed and no games of that season are stored" in out


def test_same_backfill_already_running_is_refused(db: Engine, nba: Stub, capsys) -> None:
    """A live parent row with the same arguments -> exit 1 and no calls."""
    with db.begin() as conn:
        conn.execute(
            insert(IngestionRun).values(
                job_type="backfill",
                scope_key="seasons=2024-25;season_types=regular,playoffs",
                status="running",
            )
        )
    assert backfill(db, nba, "--season", SEASON, *TYPES) == 1
    assert nba.seen == []
    assert "already running" in capsys.readouterr().err


def test_dead_backfill_row_does_not_block_the_next_one(db: Engine, nba: Stub) -> None:
    """A parent left 'running' by a crash 7 h ago is closed 'aborted' and the backfill runs."""
    nba.clean_game_logs()
    with db.begin() as conn:
        dead = conn.execute(
            insert(IngestionRun)
            .values(
                job_type="backfill",
                scope_key="seasons=2024-25;season_types=regular,playoffs",
                status="running",
                started_at=text("now() - interval '7 hours'"),
            )
            .returning(IngestionRun.run_id)
        ).scalar_one()
    assert backfill(db, nba, "--season", SEASON, *TYPES) == 0
    assert runs(db)[0].run_id == dead and runs(db)[0].status == "aborted"
