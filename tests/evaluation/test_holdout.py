"""Holdout guard of design 6-3 (T10 at function level; the CLI and runner tests are separate)."""

import logging

import pytest

from presagio.evaluation.holdout import (
    HOLDOUT_SEASONS,
    HoldoutLockedError,
    check_holdout,
    validate_unlock_reason,
)


def test_2025_26_is_the_holdout_season() -> None:
    """D-049 M-2."""
    assert HOLDOUT_SEASONS == ("2025-26",)


def test_development_seasons_pass_without_a_reason() -> None:
    """2023-24 and 2024-25 (and the live 2026-27) need no unlock."""
    decision = check_holdout(["2023-24", "2024-25", "2026-27"])
    assert decision.holdout_seasons == () and decision.unlocked_reason is None
    assert not decision.unlocked


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_holdout_without_reason_is_refused(reason: str | None) -> None:
    """Requesting 2025-26 alone or among others raises, naming the holdout."""
    with pytest.raises(HoldoutLockedError, match="holdout season 2025-26 is locked"):
        check_holdout(["2024-25", "2025-26"], reason)


@pytest.mark.parametrize("reason", ["final model", "H-08", "h-08 final", "H-8 final"])
def test_reason_must_name_a_hypothesis_and_say_why(reason: str) -> None:
    """A reason without H-NN, or with nothing but H-NN, keeps the holdout locked."""
    with pytest.raises(HoldoutLockedError, match="stays locked"):
        check_holdout(["2025-26"], reason)


def test_unlock_returns_and_logs_the_reason(caplog: pytest.LogCaptureFixture) -> None:
    """The reason is normalised, returned for model_versions.holdout_unlocked_reason, logged."""
    with caplog.at_level(logging.WARNING, logger="presagio.evaluation.holdout"):
        decision = check_holdout(["2025-26"], "  H-08   final model, stage 3 end ")
    assert decision.unlocked
    assert decision.unlocked_reason == "H-08 final model, stage 3 end"
    assert decision.holdout_seasons == ("2025-26",)
    assert "HOLDOUT UNLOCKED for 2025-26: H-08 final model, stage 3 end" in caplog.text
    assert validate_unlock_reason("H-11 strategy, single look") == "H-11 strategy, single look"


def test_reason_without_holdout_is_ignored() -> None:
    """An unlock reason for development seasons unlocks nothing."""
    decision = check_holdout(["2024-25"], "H-08 final")
    assert decision.unlocked_reason is None
