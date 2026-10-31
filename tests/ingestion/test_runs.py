"""ingestion_runs: job type, start/finish, status transitions, counts and errors are recorded."""

import re
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from importlib import metadata

import pytest
from sqlalchemy import Engine, insert, select, text
from sqlalchemy.orm import Session

from presagio import config
from presagio.db.models import IngestionRun
from presagio.ingestion import runs as runs_module
from presagio.ingestion.errors import BlockedError, RunAlreadyRunningError
from presagio.ingestion.runs import (
    JOB_TYPES,
    abort_stale_runs,
    current_code_version,
    has_succeeded,
    start_run,
    track_run,
)


def _row(engine: Engine, run_id: int) -> IngestionRun:
    """Load one run row."""
    with Session(engine) as session:
        return session.get(IngestionRun, run_id)


def _finished_after_start(engine: Engine, run_id: int) -> bool:
    """finished_at >= started_at, compared inside the DB (both are host-clock values, P-043)."""
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT finished_at >= started_at FROM ingestion_runs WHERE run_id = :id"),
            {"id": run_id},
        ).scalar_one()


def test_succeeded_run_records_everything(engine: Engine) -> None:
    """Criterion 4: job type, scope, start/finish, status, counts are written on success."""
    with track_run(
        engine,
        "gamelog",
        "season=2025-26;season_type=Playoffs",
        source="nba_api",
        season="2025-26",
        season_type="Playoffs",
        items_total=2,
        code_version="abc1234",
    ) as run:
        assert _row(engine, run.run_id).status == "running"
        assert _row(engine, run.run_id).finished_at is None
        run.add_api_calls(3, 1)
        run.item_succeeded(rows_written=4)
        run.item_succeeded(rows_written=4)

    row = _row(engine, run.run_id)
    assert (row.job_type, row.scope_key, row.source) == (
        "gamelog",
        "season=2025-26;season_type=Playoffs",
        "nba_api",
    )
    assert (row.season, row.season_type, row.code_version) == ("2025-26", "Playoffs", "abc1234")
    assert row.status == "succeeded" and row.finished_at is not None
    assert _finished_after_start(engine, run.run_id)
    assert (row.api_calls, row.retry_count) == (3, 1)
    assert (row.items_total, row.items_succeeded, row.items_failed, row.rows_written) == (
        2,
        2,
        0,
        8,
    )
    assert row.error_message is None and row.error_detail is None


def test_some_items_failed_is_partial(engine: Engine) -> None:
    """One failed item among successes -> 'partial' with the last error and the failed keys."""
    with track_run(
        engine, "boxscore", "date_et=2026-10-21", target_date_et=date(2026, 10, 21)
    ) as run:
        run.item_succeeded(rows_written=2)
        run.item_failed("game_id=0022600001", ValueError("missing team stats"))

    row = _row(engine, run.run_id)
    assert row.status == "partial"
    assert row.target_date_et == date(2026, 10, 21)
    assert (row.items_succeeded, row.items_failed) == (1, 1)
    assert row.error_message == "ValueError: missing team stats"
    assert row.error_detail == {
        "failed_items": [{"key": "game_id=0022600001", "error": "ValueError: missing team stats"}]
    }


def test_all_items_failed_is_failed(engine: Engine) -> None:
    """Every item failed and none succeeded -> 'failed'."""
    with track_run(engine, "boxscore", "date_et=2026-10-22") as run:
        run.item_failed("game_id=0022600009", "not final yet")
    assert _row(engine, run.run_id).status == "failed"


def test_mark_partial_for_unfinished_dates(engine: Engine) -> None:
    """A date with games not final yet closes 'partial' with the reason and the game ids."""
    with track_run(engine, "incremental", "date_et=2026-10-21") as run:
        run.item_succeeded()
        run.mark_partial("2 games not final", pending_game_ids=["0022600011", "0022600012"])
    row = _row(engine, run.run_id)
    assert row.status == "partial" and row.error_message == "2 games not final"
    assert row.error_detail == {"pending_game_ids": ["0022600011", "0022600012"]}
    assert not has_succeeded(engine, "incremental", "date_et=2026-10-21")


def test_exception_closes_failed_and_propagates(engine: Engine) -> None:
    """Any exception in the block -> 'failed' with a one-line error; the exception is re-raised."""
    with pytest.raises(KeyError), track_run(engine, "schedule", "season=2023-24") as run:
        raise KeyError("leagueSchedule")
    row = _row(engine, run.run_id)
    assert row.status == "failed" and row.error_message == "KeyError: 'leagueSchedule'"
    assert _finished_after_start(engine, run.run_id)


def test_blocked_closes_aborted_and_stops_the_parent(engine: Engine) -> None:
    """403/429 in a child unit -> child and parent both 'aborted', exception reaches the top."""
    with (
        pytest.raises(BlockedError),
        track_run(engine, "backfill", "cli") as parent,
        track_run(engine, "schedule", "season=2024-25", parent=parent) as child,
    ):
        raise BlockedError("nba_api/scheduleleaguev2: HTTP 403", http_status=403)
    assert _row(engine, child.run_id).parent_run_id == parent.run_id
    assert _row(engine, child.run_id).status == "aborted"
    assert _row(engine, parent.run_id).status == "aborted"
    assert _row(engine, parent.run_id).error_message.startswith("BlockedError: ")


def test_one_running_row_per_unit(engine: Engine) -> None:
    """A second run of the same job_type + scope_key while one is running is refused."""
    with track_run(engine, "schedule", "season=2025-26"):
        with (
            pytest.raises(RunAlreadyRunningError),
            track_run(engine, "schedule", "season=2025-26"),
        ):
            pass
        with track_run(engine, "schedule", "season=2024-25"):  # other unit: fine
            pass
    with track_run(engine, "schedule", "season=2025-26"):  # after it finished: fine
        pass


def test_stale_running_rows_are_aborted_before_a_new_run(engine: Engine) -> None:
    """A 'running' row older than the limit (dead process) is closed 'aborted'; fresh ones stay."""
    with engine.begin() as conn:
        stale_id, fresh_id = (
            conn.execute(
                insert(IngestionRun)
                .values(
                    job_type="schedule",
                    scope_key=scope,
                    status="running",
                    started_at=text(f"now() - interval '{hours} hours'"),
                )
                .returning(IngestionRun.run_id)
            ).scalar_one()
            for scope, hours in (("season=2025-26", 7), ("season=2024-25", 1))
        )
    with track_run(engine, "schedule", "season=2025-26", stale_after_hours=6) as run:
        pass
    stale = _row(engine, stale_id)
    assert stale.status == "aborted" and stale.error_message.startswith("stale:")
    assert _row(engine, run.run_id).status == "succeeded"
    assert _row(engine, fresh_id).status == "running"
    with pytest.raises(RunAlreadyRunningError):
        start_run(engine, "schedule", "season=2024-25")
    assert abort_stale_runs(engine, 6) == 0


def test_has_succeeded_drives_resume(engine: Engine) -> None:
    """Only a 'succeeded' row marks the unit done (query Q9)."""
    assert not has_succeeded(engine, "schedule", "season=2025-26")
    with track_run(engine, "schedule", "season=2025-26"):
        pass
    assert has_succeeded(engine, "schedule", "season=2025-26")
    with engine.connect() as conn:
        assert conn.execute(select(IngestionRun.status)).scalars().all() == ["succeeded"]


def _insert_running(engine: Engine, job_type: str, scope_key: str, hours_ago: float) -> int:
    """A 'running' row started hours_ago (as a dead process would leave it)."""
    with engine.begin() as conn:
        return conn.execute(
            insert(IngestionRun)
            .values(
                job_type=job_type,
                scope_key=scope_key,
                status="running",
                started_at=text(f"now() - interval '{hours_ago} hours'"),
            )
            .returning(IngestionRun.run_id)
        ).scalar_one()


@pytest.fixture
def stale_run_hours(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[str], None]]:
    """Set STALE_RUN_HOURS for get_settings(); the cached settings are reset afterwards."""

    def _set(value: str) -> None:
        """Change the environment variable and drop the cached Settings."""
        monkeypatch.setenv("STALE_RUN_HOURS", value)
        config.get_settings.cache_clear()

    yield _set
    config.get_settings.cache_clear()


def test_dead_running_row_does_not_block_with_default_limit(engine: Engine) -> None:
    """Without stale_after_hours, track_run uses STALE_RUN_HOURS (6): a 7 h old row is closed."""
    dead = _insert_running(engine, "schedule", "season=2025-26", hours_ago=7)
    with track_run(engine, "schedule", "season=2025-26") as run:  # no stale_after_hours given
        pass
    assert _row(engine, dead).status == "aborted"
    assert _row(engine, run.run_id).status == "succeeded"


def test_default_limit_is_read_from_settings(engine: Engine, stale_run_hours) -> None:
    """STALE_RUN_HOURS=2 closes a 3 h old row; the default 6 would have refused the new run."""
    _insert_running(engine, "gamelog", "season=2024-25;season_type=Playoffs", hours_ago=3)
    with (
        pytest.raises(RunAlreadyRunningError),
        track_run(engine, "gamelog", "season=2024-25;season_type=Playoffs"),
    ):
        pass
    stale_run_hours("2")
    with track_run(engine, "gamelog", "season=2024-25;season_type=Playoffs") as run:
        pass
    assert _row(engine, run.run_id).status == "succeeded"


def test_finish_does_not_revive_a_row_closed_by_stale_cleanup(engine: Engine) -> None:
    """finish() only updates 'running' rows: a row aborted meanwhile stays aborted (D-032 b)."""
    with track_run(engine, "schedule", "season=2023-24") as run:
        # Another process decides this run is dead and closes it (backdated so it counts as old).
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE ingestion_runs SET started_at = now() - interval '7 hours' "
                    "WHERE run_id = :id"
                ),
                {"id": run.run_id},
            )
        assert abort_stale_runs(engine, 6) == 1
        run.item_succeeded(rows_written=5)
    row = _row(engine, run.run_id)
    assert row.status == "aborted" and row.error_message.startswith("stale:")
    assert row.rows_written == 0  # the late finish wrote nothing
    assert run.status == "aborted"
    assert not has_succeeded(engine, "schedule", "season=2023-24")


def test_finish_reports_whether_it_closed_the_row(engine: Engine) -> None:
    """finish() returns True when it closed the row, False when someone else already had."""
    run = start_run(engine, "teams", "nba_api")
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE ingestion_runs SET status = 'aborted', finished_at = now() "
                "WHERE run_id = :id"
            ),
            {"id": run.run_id},
        )
    assert run.finish("succeeded") is False
    assert _row(engine, run.run_id).status == "aborted"
    other = start_run(engine, "teams", "nba_api")
    assert other.finish("succeeded") is True


def test_nested_runs_become_children_and_record_code_version(engine: Engine) -> None:
    """A track_run inside another is its child by default; every row gets code_version."""
    with track_run(engine, "backfill", "seasons=2025-26") as parent:
        with track_run(engine, "schedule", "season=2025-26") as child:
            pass
        with track_run(engine, "teams", "nba_api", parent=None) as orphan:  # explicit None wins
            pass
    with track_run(engine, "schedule", "season=2024-25") as after:  # outside: no parent
        pass
    assert _row(engine, child.run_id).parent_run_id == parent.run_id
    assert _row(engine, orphan.run_id).parent_run_id is None
    assert _row(engine, after.run_id).parent_run_id is None
    versions = {_row(engine, r.run_id).code_version for r in (parent, child, orphan, after)}
    assert versions == {current_code_version()} and None not in versions


def test_unknown_job_type_is_refused(engine: Engine) -> None:
    """job_type must be one of the documented values (a typo would break resume silently)."""
    assert {"teams", "schedule", "gamelog", "boxscore", "backfill", "incremental"} <= JOB_TYPES
    with pytest.raises(ValueError, match="unknown job_type"):
        start_run(engine, "schedules", "season=2025-26")


def test_code_version_is_git_commit_or_package_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """Short git hash in a checkout; the installed package version when git fails."""
    current_code_version.cache_clear()
    try:
        commit = current_code_version()
        assert commit is not None and re.fullmatch(r"[0-9a-f]{7,40}", commit)

        def no_git(*args: object, **kwargs: object) -> None:
            """Behave as if git is not installed."""
            raise FileNotFoundError("git")

        monkeypatch.setattr(runs_module.subprocess, "run", no_git)
        current_code_version.cache_clear()
        assert current_code_version() == metadata.version("presagio")
    finally:
        current_code_version.cache_clear()


# --- P-043: started_at and finished_at are host-clock values -----------------------------------


class HostClock:
    """An injectable host clock: the test moves .now between calls."""

    def __init__(self, now: datetime) -> None:
        """Start at the given moment."""
        self.now = now

    def __call__(self) -> datetime:
        """The current (fake) host time."""
        return self.now


T_START = datetime(2026, 10, 24, 1, 46, 25, 840000, tzinfo=UTC)  # like run 119's capture


def _db_now(engine: Engine) -> datetime:
    """The database clock."""
    with engine.connect() as conn:
        return conn.execute(text("SELECT now()")).scalar_one()


def test_run_timestamps_come_from_the_host_clock(engine: Engine) -> None:
    """P-043 (f): the injected host time lands in started_at/finished_at, not the DB's now()."""
    clock = HostClock(T_START)
    assert _db_now(engine) - T_START > timedelta(days=1)  # the DB clock is nowhere near it
    with track_run(engine, "odds", "the_odds_api", clock=clock) as run:
        assert run.started_at == T_START
        assert _row(engine, run.run_id).started_at == T_START
        clock.now = T_START + timedelta(seconds=0.8)
    row = _row(engine, run.run_id)
    assert row.status == "succeeded"
    assert (row.started_at, row.finished_at) == (T_START, T_START + timedelta(seconds=0.8))
    assert _finished_after_start(engine, run.run_id)
    # has_succeeded(started_after=) now compares host time with host time.
    assert has_succeeded(engine, "odds", "the_odds_api", started_after=T_START)
    assert not has_succeeded(
        engine, "odds", "the_odds_api", started_after=T_START + timedelta(microseconds=1)
    )


def test_default_clock_is_the_host_now(engine: Engine) -> None:
    """Without injection, started_at is datetime.now(UTC) of this process (within a minute)."""
    before = datetime.now(UTC)
    run = start_run(engine, "teams", "nba_api")
    run.finish("succeeded")
    row = _row(engine, run.run_id)
    assert before - timedelta(minutes=1) <= row.started_at <= datetime.now(UTC)
    assert row.started_at <= row.finished_at <= datetime.now(UTC) + timedelta(minutes=1)


def test_a_clock_that_steps_back_cannot_break_finished_after_started(engine: Engine) -> None:
    """If the host clock is set back during a run, finished_at is clamped to started_at."""
    clock = HostClock(T_START)
    with track_run(engine, "teams", "nba_api", clock=clock) as run:
        clock.now = T_START - timedelta(minutes=5)
    row = _row(engine, run.run_id)
    assert row.started_at == row.finished_at == T_START


def test_stale_rows_are_closed_with_the_host_clock(engine: Engine) -> None:
    """abort_stale_runs compares age on the DB clock (hours) but stamps finished_at by host."""
    dead = _insert_running(engine, "schedule", "season=2025-26", hours_ago=7)
    at = _db_now(engine) + timedelta(minutes=17)  # a host clock 17 min ahead of the DB VM
    assert abort_stale_runs(engine, 6, clock=HostClock(at)) == 1
    row = _row(engine, dead)
    assert row.status == "aborted" and row.finished_at == at
    assert _finished_after_start(engine, dead)
