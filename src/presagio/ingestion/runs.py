"""ingestion_runs bookkeeping: open a run, count calls and items, close it with a status."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from typing import Any

from sqlalchemy import Engine, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from presagio.db.models import IngestionRun
from presagio.ingestion.errors import BlockedError, RunAlreadyRunningError

RUNS = IngestionRun.__table__

# Status values allowed by ck_ingestion_runs_status_valid (schema v1 ch. 4).
RUNNING = "running"
SUCCEEDED = "succeeded"
PARTIAL = "partial"
FAILED = "failed"
ABORTED = "aborted"  # stopped on purpose (403/429, D-024) or left stale by a dead process
FINAL_STATUSES = frozenset({SUCCEEDED, PARTIAL, FAILED, ABORTED})

# Keep error_message to one short line; details belong in error_detail.
MAX_ERROR_MESSAGE = 500


def error_line(exc: BaseException) -> str:
    """Format an exception as the single line stored in ingestion_runs.error_message."""
    first = (str(exc).strip().splitlines() or [""])[0]
    line = f"{type(exc).__name__}: {first}" if first else type(exc).__name__
    return line[:MAX_ERROR_MESSAGE]


class RunTracker:
    """An open ingestion_runs row; counters accumulate in memory and are written on finish."""

    def __init__(self, engine: Engine, run_id: int, job_type: str, scope_key: str) -> None:
        """Wrap an already inserted 'running' row."""
        self.engine = engine
        self.run_id = run_id
        self.job_type = job_type
        self.scope_key = scope_key
        self.api_calls = 0
        self.retry_count = 0
        self.items_total: int | None = None
        self.items_succeeded = 0
        self.items_failed = 0
        self.rows_written = 0
        self.error_message: str | None = None
        self.failed_items: list[dict[str, str]] = []
        self.detail: dict[str, Any] = {}
        self.status: str = RUNNING
        self._partial_reason: str | None = None

    def add_api_calls(self, calls: int, retries: int) -> None:
        """Count external calls made for this run (retries are included in calls)."""
        self.api_calls += calls
        self.retry_count += retries

    def item_succeeded(self, rows_written: int = 0) -> None:
        """Record one finished work item and the clean rows it inserted or updated."""
        self.items_succeeded += 1
        self.rows_written += rows_written

    def item_failed(self, key: str, error: BaseException | str) -> None:
        """Record one failed work item; the run keeps going and ends 'partial'."""
        message = error if isinstance(error, str) else error_line(error)
        self.items_failed += 1
        self.failed_items.append({"key": key, "error": message[:MAX_ERROR_MESSAGE]})
        self.error_message = message[:MAX_ERROR_MESSAGE]

    def mark_partial(self, reason: str, **detail: Any) -> None:
        """Force a 'partial' ending, e.g. a date whose games are not all final yet."""
        self._partial_reason = reason[:MAX_ERROR_MESSAGE]
        self.detail.update(detail)

    def outcome(self) -> str:
        """Status for a run whose body finished without raising."""
        if self.items_failed and not self.items_succeeded:
            return FAILED
        if self.items_failed or self._partial_reason is not None:
            return PARTIAL
        return SUCCEEDED

    def finish(self, status: str, error: BaseException | None = None) -> None:
        """Close the row with a final status, the counters and the last error."""
        if status not in FINAL_STATUSES:
            raise ValueError(f"not a final status: {status!r}")
        if self.status != RUNNING:
            raise RuntimeError(f"run {self.run_id} is already closed as {self.status!r}")
        if error is not None:
            self.error_message = error_line(error)
        elif self.error_message is None and self._partial_reason is not None:
            self.error_message = self._partial_reason
        detail = dict(self.detail)
        if self.failed_items:
            detail["failed_items"] = self.failed_items
        with self.engine.begin() as conn:
            conn.execute(
                update(RUNS)
                .where(RUNS.c.run_id == self.run_id)
                .values(
                    status=status,
                    # DB clock for both ends of the run, so finished_at >= started_at holds.
                    finished_at=func.now(),
                    api_calls=self.api_calls,
                    retry_count=self.retry_count,
                    items_total=self.items_total,
                    items_succeeded=self.items_succeeded,
                    items_failed=self.items_failed,
                    rows_written=self.rows_written,
                    error_message=self.error_message,
                    error_detail=detail or None,
                )
            )
        self.status = status


def abort_stale_runs(engine: Engine, older_than_hours: float, job_type: str | None = None) -> int:
    """Close 'running' rows older than the limit as 'aborted' (crashed processes); return count."""
    stmt = (
        update(RUNS)
        .where(RUNS.c.status == RUNNING)
        .where(RUNS.c.started_at < func.now() - timedelta(hours=older_than_hours))
        .values(
            status=ABORTED,
            finished_at=func.now(),
            error_message=f"stale: still running after {older_than_hours:g} h (process died?)",
        )
    )
    if job_type is not None:
        stmt = stmt.where(RUNS.c.job_type == job_type)
    with engine.begin() as conn:
        result = conn.execute(stmt)
    return result.rowcount


def start_run(
    engine: Engine,
    job_type: str,
    scope_key: str,
    *,
    source: str | None = None,
    parent: RunTracker | None = None,
    season: str | None = None,
    season_type: str | None = None,
    target_date_et: date | None = None,
    items_total: int | None = None,
    code_version: str | None = None,
) -> RunTracker:
    """Insert a 'running' row; raises RunAlreadyRunningError if this unit is already running."""
    values = {
        "job_type": job_type,
        "scope_key": scope_key,
        "source": source,
        "parent_run_id": parent.run_id if parent is not None else None,
        "season": season,
        "season_type": season_type,
        "target_date_et": target_date_et,
        "items_total": items_total,
        "code_version": code_version,
        "status": RUNNING,
    }
    try:
        with engine.begin() as conn:
            stmt = insert(RUNS).values(**values).returning(RUNS.c.run_id)
            run_id = conn.execute(stmt).scalar_one()
    except IntegrityError as exc:
        if "uq_ingestion_runs_one_running" in str(exc.orig):
            raise RunAlreadyRunningError(f"{job_type} {scope_key} is already running") from None
        raise
    tracker = RunTracker(engine, run_id, job_type, scope_key)
    tracker.items_total = items_total
    return tracker


@contextmanager
def track_run(
    engine: Engine,
    job_type: str,
    scope_key: str,
    *,
    stale_after_hours: float | None = None,
    **fields: Any,
) -> Iterator[RunTracker]:
    """Run the block as one ingestion_runs row: succeeded/partial, failed, or aborted on block."""
    if stale_after_hours is not None:
        abort_stale_runs(engine, stale_after_hours, job_type=job_type)
    run = start_run(engine, job_type, scope_key, **fields)
    try:
        yield run
    except BlockedError as exc:
        # 403/429: the source is refusing us. Close as aborted (D-024) and let the caller stop.
        run.finish(ABORTED, error=exc)
        raise
    except BaseException as exc:
        run.finish(FAILED, error=exc)
        raise
    else:
        run.finish(run.outcome())


def has_succeeded(engine: Engine, job_type: str, scope_key: str) -> bool:
    """True if this work unit already has a 'succeeded' run (resume check, query Q9)."""
    stmt = (
        select(RUNS.c.run_id)
        .where(RUNS.c.job_type == job_type, RUNS.c.scope_key == scope_key)
        .where(RUNS.c.status == SUCCEEDED)
        .limit(1)
    )
    with engine.connect() as conn:
        return conn.execute(stmt).first() is not None
