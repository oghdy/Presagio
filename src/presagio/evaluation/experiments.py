"""Registry of what `presagio backtest run --experiment <id>` can run (D-043).

An experiment id names a predictor factory. Win baselines (design 5-1) are registered under
their baseline ids (B0..B3), total baselines (design 5-3) under T-Bn (T-B0 here; T-B1 needs
the team-stat features, after P-035), hypotheses under H-NN. H-00 is the framework dummy of
P-032: it has no ModelSpec, so it cannot be stored.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from presagio.evaluation.dummy import ConstantPredictor
from presagio.evaluation.walkforward import Predictor
from presagio.models.baselines import AlwaysHomeBaseline, MarketBaseline, SeasonWinRateBaseline
from presagio.models.elo import EloBaseline
from presagio.models.spec import ModelSpec
from presagio.models.totals import LeagueTotalBaseline


@dataclass(frozen=True)
class Experiment:
    """One registered experiment: its id, what it runs and a one-line description."""

    id: str
    description: str
    factory: Callable[[], Predictor]

    def make(self) -> Predictor:
        """A fresh predictor instance."""
        return self.factory()


def model_spec(predictor: object) -> ModelSpec | None:
    """The predictor's ModelSpec, or None when it is not a storable model (the dummy)."""
    spec = getattr(predictor, "spec", None)
    return spec if isinstance(spec, ModelSpec) else None


EXPERIMENTS: dict[str, Experiment] = {
    e.id: e
    for e in (
        Experiment(
            "H-00",
            "framework dummy (p_home 0.5, total = league mean); not storable",
            ConstantPredictor,
        ),
        Experiment("B0", "always home: season cumulative home win rate", AlwaysHomeBaseline),
        Experiment("B1", "season win rate difference, clipped", SeasonWinRateBaseline),
        Experiment(
            "B2",
            "Elo rating (design 5-2): K=20, HCA=100, season carry 0.75, warmed up on earlier "
            "seasons",
            EloBaseline,
        ),
        Experiment("B3", "prediction-time market probability (R3)", MarketBaseline),
        Experiment(
            "T-B0",
            "league mean total of the last 100 games (design 5-3 T-B0); totals only",
            LeagueTotalBaseline,
        ),
    )
}
