"""ingestion_runs: job type, start/finish, status transitions, counts and errors are recorded."""

from datetime import date

import pytest
from sqlalchemy import Engine, insert, select, text
from sqlalchemy.orm import Session

from presagio.db.models import IngestionRun
from presagio.ingestion.errors import BlockedError, RunAlreadyRunningError
from presagio.ingestion.runs import abort_stale_runs, has_succeeded, start_run, track_run


def _row(engine: Engine, run_id: int) -> IngestionRun:
    """Load one run row."""
    with Session(engine) as session:
        return session.get(IngestionRun, run_id)


def _finished_after_start(engine: Engine, run_id: int) -> bool:
    """finished_at >= started_at, compared inside the DB (both are DB-clock values)."""
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
