"""Shared loader pieces: upsert outcome counts and the run-detail format for issues."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Table, literal_column

from presagio.ingestion.runs import RunTracker
from presagio.ingestion.sources.base import QualityIssue

# Rows per INSERT statement; keeps bind parameters far below PostgreSQL's 65,535 limit.
CHUNK_SIZE = 500
# xmax is 0 only on a row this statement inserted (not one it updated), a PostgreSQL detail.
INSERTED = literal_column("xmax = 0").label("inserted")


@dataclass(frozen=True)
class UpsertCounts:
    """What an upsert did: new rows, changed rows, rows left alone by the fetched_at guard."""

    inserted: int = 0
    updated: int = 0
    stale: int = 0

    @property
    def written(self) -> int:
        """Rows inserted or updated."""
        return self.inserted + self.updated

    def __add__(self, other: UpsertCounts) -> UpsertCounts:
        """Sum two counts (one per statement chunk)."""
        return UpsertCounts(
            self.inserted + other.inserted, self.updated + other.updated, self.stale + other.stale
        )

    def as_dict(self) -> dict[str, int]:
        """JSON-ready form for ingestion_runs.error_detail."""
        return {"inserted": self.inserted, "updated": self.updated, "stale": self.stale}


def chunks(rows: Sequence[dict[str, Any]], size: int = CHUNK_SIZE) -> Iterable[Sequence[Any]]:
    """Consecutive slices of at most size rows."""
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def count_returned(returned: Sequence[Any], attempted: int) -> UpsertCounts:
    """Turn RETURNING (..., inserted) rows into counts; missing rows were stopped by the guard."""
    inserted = sum(1 for row in returned if row.inserted)
    return UpsertCounts(inserted, len(returned) - inserted, attempted - len(returned))


def record_issues(run: RunTracker, issues: Sequence[QualityIssue]) -> None:
    """Append quality issues to the run's error_detail (they never fail the run)."""
    if issues:
        run.detail.setdefault("quality_issues", []).extend(i.as_dict() for i in issues)


def table_of(model: Any) -> Table:
    """The Core table of an ORM model."""
    return model.__table__
