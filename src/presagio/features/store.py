"""Write features rows, append only (design 8-1, rule R4, D-030).

INSERT ... ON CONFLICT (game_id, as_of, feature_version) DO NOTHING; never UPDATE. A row that
already exists is compared: equal values count as already stored, different values are
counted as differing with an example `feature_id:game_id:<first differing key>` (at most
MAX_EXAMPLES) and are not written (T11; the feature_drift check reports the same thing).
Existing rows are read one chunk at a time (CHUNK keys per SELECT), so a rerun over a
whole backfill costs a handful of round trips, not one per row.

Checked before anything is written (the whole batch is refused otherwise):
- as_of < start_time_utc of every row (R4; spans two tables, so the loader guards it and
  the features_after_tipoff check re-checks it),
- as_of <= computed_at, where computed_at is the Python UTC clock (the same clock as as_of,
  never the database's now(): D-030 lesson),
- origin in ORIGINS, snapshot_kind in SNAPSHOT_KINDS, code_version = the git commit
  (presagio.ingestion.runs.current_code_version) or "unknown".
The live cap computed_at - as_of <= 2 h is the database CHECK live_computed_within_2h; its
IntegrityError is reported as a FeatureStoreError.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from presagio.db.models.modeling import ORIGINS, SNAPSHOT_KINDS, Feature
from presagio.features.compute import FeatureRow
from presagio.ingestion.runs import current_code_version

logger = logging.getLogger("presagio.features")

MAX_EXAMPLES = 5
CHUNK = 500
KEY = ("game_id", "as_of", "feature_version")


class FeatureStoreError(RuntimeError):
    """The batch cannot be stored as it is (time rule, bad origin or kind, DB constraint)."""


@dataclass
class FeatureStoreSummary:
    """What happened to the batch."""

    inserted: int = 0
    already_stored: int = 0  # same key, same values
    differing: int = 0  # same key, different values: reported, never written
    examples: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        """Rows the batch tried to store."""
        return self.inserted + self.already_stored + self.differing


def _chunks(rows: Sequence[Any], size: int | None = None) -> Iterator[Sequence[Any]]:
    """Row batches small enough for one multi-row INSERT or one keyed SELECT."""
    size = CHUNK if size is None else size
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


# The stored rows of a chunk's keys in one round trip (a superset by the three key columns,
# matched exactly in Python): one SELECT per chunk, never one per row.
EXISTING_SQL = text(
    """
    SELECT feature_id, game_id, as_of, feature_version, "values"
    FROM features
    WHERE game_id = ANY(:game_ids) AND as_of = ANY(:as_ofs) AND feature_version = ANY(:versions)
    """
)


def _existing(
    conn: Connection, chunk: Sequence[FeatureRow]
) -> dict[tuple[str, datetime, str], tuple[int, dict[str, Any]]]:
    """(game_id, as_of, feature_version) -> (feature_id, values) for the stored rows of a chunk."""
    params = {
        "game_ids": sorted({r.game_id for r in chunk}),
        "as_ofs": sorted({r.as_of for r in chunk}),
        "versions": sorted({r.feature_version for r in chunk}),
    }
    return {
        (r.game_id, r.as_of, r.feature_version): (r.feature_id, r.values)
        for r in conn.execute(EXISTING_SQL, params)
    }


def first_difference(stored: dict[str, Any], new: dict[str, Any]) -> str | None:
    """The first key (sorted) whose value differs, or None when the dicts are equal."""
    keys = sorted(k for k in stored.keys() | new.keys() if stored.get(k, ...) != new.get(k, ...))
    return keys[0] if keys else None


def store_features(
    conn: Connection,
    rows: Sequence[FeatureRow],
    origin: str,
    snapshot_kind: str = "daily",
    computed_at: datetime | None = None,
    code_version: str | None = None,
) -> FeatureStoreSummary:
    """Insert the rows (ON CONFLICT DO NOTHING) and report inserted / equal / differing."""
    if origin not in ORIGINS:
        raise FeatureStoreError(f"origin must be one of {ORIGINS}, got {origin!r}")
    if snapshot_kind not in SNAPSHOT_KINDS:
        raise FeatureStoreError(
            f"snapshot_kind must be one of {sorted(SNAPSHOT_KINDS)}, got {snapshot_kind!r}"
        )
    computed_at = computed_at or datetime.now(UTC)
    if computed_at.tzinfo is None:
        raise FeatureStoreError(f"computed_at must be an aware timestamp, got {computed_at!r}")
    version = code_version or current_code_version() or "unknown"
    for row in rows:
        if not row.as_of < row.start_time_utc:
            raise FeatureStoreError(
                f"features row of {row.game_id} has as_of {row.as_of.isoformat()} at or after "
                f"its start {row.start_time_utc.isoformat()} (rule R4); nothing stored"
            )
        if not row.as_of <= computed_at:
            raise FeatureStoreError(
                f"features row of {row.game_id} has as_of {row.as_of.isoformat()} after "
                f"computed_at {computed_at.isoformat()}; nothing stored"
            )
    summary = FeatureStoreSummary()
    if not rows:
        return summary
    table = Feature.__table__
    records = [
        {
            "game_id": row.game_id,
            "as_of": row.as_of,
            "feature_version": row.feature_version,
            "snapshot_kind": snapshot_kind,
            "origin": origin,
            "values": row.values,
            "input_fingerprint": row.input_fingerprint,
            "computed_at": computed_at,
            "code_version": version,
        }
        for row in rows
    ]
    inserted: set[tuple[Any, ...]] = set()
    try:
        with conn.begin_nested():
            for chunk in _chunks(records):
                stmt = (
                    pg_insert(table)
                    .values(list(chunk))
                    .on_conflict_do_nothing(index_elements=list(KEY))
                    .returning(*(table.c[k] for k in KEY))
                )
                inserted.update(tuple(r) for r in conn.execute(stmt))
    except IntegrityError as exc:
        raise FeatureStoreError(f"database refused the features batch: {exc.orig}") from exc
    summary.inserted = len(inserted)
    pending = [row for row in rows if (row.game_id, row.as_of, row.feature_version) not in inserted]
    for chunk in _chunks(pending):
        existing = _existing(conn, chunk)
        for row in chunk:
            feature_id, values = existing[(row.game_id, row.as_of, row.feature_version)]
            key = first_difference(values, row.values)
            if key is None:
                summary.already_stored += 1
                continue
            summary.differing += 1
            if len(summary.examples) < MAX_EXAMPLES:
                summary.examples.append(f"{feature_id}:{row.game_id}:{key}")
    logger.info(
        "stored features %s (%s, %s): +%d =%d !%d",
        rows[0].feature_version,
        origin,
        snapshot_kind,
        summary.inserted,
        summary.already_stored,
        summary.differing,
    )
    for example in summary.examples:
        logger.warning("stored feature values differ (not overwritten): %s", example)
    return summary
