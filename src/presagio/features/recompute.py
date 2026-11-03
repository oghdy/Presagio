"""The recompute function the feature_drift quality check (17) is given (D-056, P-035).

presagio.quality.checks never imports this package: presagio.quality.cli injects a
FeatureRecomputer into CheckSettings(feature_recompute=...). A stored row is recomputed at
its own as_of under its own feature_version (design 8-1: stored rows are never overwritten;
a difference is reported by the check). An unknown version returns None, which the check
counts as "could not be recomputed".

Two callables with the FeatureRecompute signature:

- recompute_stored(conn, stored): one compute_features call per row (reads the whole
  history every time; fine for a day's worth of rows).
- FeatureRecomputer(): the same values, but rows sharing (D(g), as_of, feature_version)
  are computed together with compute_day, whose rows equal compute_features' row by row
  (tested). The cache lives exactly as long as the transaction it was filled in (a weak
  reference to the Connection's current transaction, or to the Connection when none is
  open): the check runs inside one read-only transaction, so a run never sees another
  run's values and a cache entry never outlives the data it was computed from.
"""

from __future__ import annotations

import weakref
from datetime import date, datetime
from typing import Any, Protocol

from sqlalchemy import Connection, text

from presagio.features.compute import compute_day, compute_features, et_date
from presagio.features.manifest import FEATURE_VERSIONS

GAME_START_SQL = text("SELECT start_time_utc FROM games WHERE game_id = :game_id")


class StoredFeatureLike(Protocol):
    """What the check hands over (presagio.quality.checks.StoredFeature, structurally)."""

    game_id: str
    as_of: Any
    feature_version: str


def recompute_stored(conn: Connection, stored: StoredFeatureLike) -> dict[str, Any] | None:
    """The values of a stored row computed again at its as_of; None for an unknown version."""
    if stored.feature_version not in FEATURE_VERSIONS:
        return None
    return compute_features(
        conn, stored.game_id, stored.as_of, version=stored.feature_version
    ).values


DayKey = tuple[date, datetime, str]


class FeatureRecomputer:
    """recompute_stored with a per-transaction cache of compute_day results."""

    def __init__(self) -> None:
        """Start with an empty cache (filled per transaction, dropped with it)."""
        self._cache: weakref.WeakKeyDictionary[Any, dict[DayKey, dict[str, dict[str, Any]]]] = (
            weakref.WeakKeyDictionary()
        )

    def _days(self, conn: Connection) -> dict[DayKey, dict[str, dict[str, Any]]]:
        """The cache of the connection's current transaction (or of the connection)."""
        scope = conn.get_transaction() or conn
        try:
            return self._cache[scope]
        except KeyError:
            self._cache[scope] = {}
            return self._cache[scope]

    def __call__(self, conn: Connection, stored: StoredFeatureLike) -> dict[str, Any] | None:
        """The row's values from the cached day, computing the day once; None if unknown."""
        if stored.feature_version not in FEATURE_VERSIONS:
            return None
        start = conn.execute(GAME_START_SQL, {"game_id": stored.game_id}).scalar_one_or_none()
        if start is None:
            return recompute_stored(conn, stored)  # raises FeatureComputeError like before
        days = self._days(conn)
        key = (et_date(start), stored.as_of, stored.feature_version)
        if key not in days:
            rows = compute_day(conn, key[0], stored.as_of, version=stored.feature_version)
            days[key] = {row.game_id: row.values for row in rows}
        values = days[key].get(stored.game_id)
        if values is None:  # a game compute_day leaves out (time or teams TBD now)
            return recompute_stored(conn, stored)
        return values
