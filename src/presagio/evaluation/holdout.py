"""Holdout season guard (modeling design v1.2 6-3, D-049 M-2).

2025-26 is the holdout season. Predicting its games and aggregating their metrics is refused
unless the caller passes an unlock reason naming the hypothesis (e.g. "H-08 final model,
stage 3 end"). The reason is returned and logged so P-033 can store it in
model_versions.holdout_unlocked_reason and docs/experiments.md.

What stays allowed without unlocking: computing features and state (Elo, rolling, previous
season values) from 2025-26 results, and training on 2025-26 when predicting 2026-27 (6-3
items 2 and 6). The guard is about which seasons are predicted and scored, not which results
are read as inputs.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

logger = logging.getLogger("presagio.evaluation.holdout")

HOLDOUT_SEASONS = ("2025-26",)
# An unlock reason names a registered hypothesis (H-01..H-12, design 7-1) and says why.
HYPOTHESIS_RE = re.compile(r"\bH-\d{2}\b")


class HoldoutLockedError(Exception):
    """A holdout season was requested without a valid unlock reason."""


@dataclass(frozen=True)
class HoldoutDecision:
    """What the guard allowed: the seasons, which of them are holdout, and why."""

    seasons: tuple[str, ...]
    holdout_seasons: tuple[str, ...]  # requested seasons that are holdout seasons
    unlocked_reason: str | None  # set only when a holdout season was requested and unlocked

    @property
    def unlocked(self) -> bool:
        """True when a holdout season is being predicted under an unlock reason."""
        return self.unlocked_reason is not None


def validate_unlock_reason(reason: str) -> str:
    """The stripped reason; ValueError unless it names a hypothesis and gives a reason."""
    cleaned = " ".join(reason.split())
    if not HYPOTHESIS_RE.search(cleaned):
        raise ValueError(
            f"unlock reason must name the hypothesis (e.g. 'H-08 final model'): {reason!r}"
        )
    if not HYPOTHESIS_RE.sub("", cleaned).strip(" ,.:;-"):
        raise ValueError(f"unlock reason must say why, not only the hypothesis: {reason!r}")
    return cleaned


def check_holdout(
    seasons: Sequence[str],
    unlock_reason: str | None = None,
    holdout_seasons: Sequence[str] = HOLDOUT_SEASONS,
) -> HoldoutDecision:
    """Allow the seasons, or raise HoldoutLockedError when a holdout season lacks a reason."""
    requested = tuple(seasons)
    locked = tuple(s for s in requested if s in holdout_seasons)
    if not locked:
        if unlock_reason is not None:
            logger.info("unlock reason ignored: no holdout season requested (%s)", unlock_reason)
        return HoldoutDecision(requested, (), None)
    if unlock_reason is None or not unlock_reason.strip():
        raise HoldoutLockedError(
            f"holdout season {', '.join(locked)} is locked (design 6-3, D-049 M-2); pass "
            '--unlock-holdout "<hypothesis and reason>" to evaluate it once'
        )
    try:
        reason = validate_unlock_reason(unlock_reason)
    except ValueError as exc:
        raise HoldoutLockedError(
            f"holdout season {', '.join(locked)} stays locked: {exc}"
        ) from None
    logger.warning("HOLDOUT UNLOCKED for %s: %s", ", ".join(locked), reason)
    return HoldoutDecision(requested, locked, reason)
