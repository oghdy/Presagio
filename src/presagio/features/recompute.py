"""The recompute function the feature_drift quality check (17) is given (D-056, P-035).

presagio.quality.checks never imports this package: presagio.quality.cli injects
`recompute_stored` into CheckSettings(feature_recompute=...). A stored row is recomputed at
its own as_of under its own feature_version (design 8-1: stored rows are never overwritten;
a difference is reported by the check). An unknown version returns None, which the check
counts as "could not be recomputed".
"""

from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy import Connection

from presagio.features.compute import compute_features
from presagio.features.manifest import FEATURE_VERSIONS


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
