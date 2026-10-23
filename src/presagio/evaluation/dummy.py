"""Dummy predictors that exercise the framework (experiment H-00), not baselines.

The real baselines B0..B3 (design 5-1) are P-033/P-034 and live in presagio.models; they
plug into the same Predictor protocol (presagio.evaluation.walkforward) structurally, without
importing presagio.evaluation (design 9-2 Q4 import boundary).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from presagio.evaluation.walkforward import (
    Prediction,
    PredictionContext,
    TargetGame,
    TrainingMatrix,
)


class ConstantPredictor:
    """p_home = 0.5 for every game; total_mean = league mean total of the visible results
    (None before the first visible result); no over probability."""

    name = "dummy-constant (p_home 0.5, total = league mean so far)"
    trains = False
    feature_version = None

    def fit(self, training: TrainingMatrix) -> None:
        """Nothing to learn."""

    def predict(
        self, as_of: datetime, games: Sequence[TargetGame], context: PredictionContext
    ) -> Mapping[str, Prediction]:
        """The same values for every game of the day."""
        results = context.results()
        mean = sum(r.total for r in results) / len(results) if results else None
        return {g.game_id: Prediction(p_home=0.5, total_mean=mean) for g in games}


class HomeRateLearner:
    """A learning dummy: p_home = home win rate of its last training matrix (refit cadence,
    minimum-training rule and training rows are what it exercises)."""

    name = "dummy-learner (training home win rate)"
    trains = True
    feature_version = "dummy-fv0"

    def __init__(self) -> None:
        """Start unfitted."""
        self.rate: float | None = None
        self.fits: list[TrainingMatrix] = []

    def fit(self, training: TrainingMatrix) -> None:
        """Home win rate of the training games (0.5 without any), kept inside (0, 1)."""
        self.fits.append(training)
        rows = training.rows
        rate = sum(r.home_won for r in rows) / len(rows) if rows else 0.5
        self.rate = min(max(rate, 0.01), 0.99)

    def predict(
        self, as_of: datetime, games: Sequence[TargetGame], context: PredictionContext
    ) -> Mapping[str, Prediction]:
        """The fitted rate for every game."""
        if self.rate is None:
            raise RuntimeError("predict before fit")
        return {g.game_id: Prediction(p_home=self.rate) for g in games}
