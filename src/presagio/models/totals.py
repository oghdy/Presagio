"""League-mean total baseline T-B0 of modeling design v1.2 5-3 (P-034).

A non-learning, totals-only predictor of the walk-forward runner (structurally a
presagio.evaluation.walkforward.Predictor; this package never imports presagio.evaluation,
design 9-2 Q4). It reads context.results() only (finished games passing R1 and R2 in start
order, Cup final excluded by D-049 M-3) and the prediction-time market of the target game
for the reference line (rule R3).

- total_mean  mean total of the pool, total_sd its sample standard deviation (None with
              fewer than two games). The pool is the last `n` (100, design 5-3 proposal)
              results of the matching kind in start order: regular results for a regular
              target; play-in and playoff results together for a postseason target
              ("postseason with postseason"), falling back to the regular pool when fewer
              than `min_postseason` (10) postseason results are visible. The pool crosses
              season boundaries: it is a league-level level, not a team rolling window, so
              design 2-4's within-season rule does not apply.
- over_prob   P(total > L) = 1 - NormalDist(total_mean, total_sd).cdf(L) when the market
              has a reference total line L at as_of; None without a line, with fewer than two
              games or a zero standard deviation.
- p_home      None: this model says nothing about the winner (predictions.p_home NULL).

An empty pool (the first prediction day before any result) means no prediction for the
game, like B3 without a market (the runner records it as not_predicted).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from statistics import NormalDist, fmean, stdev
from typing import Any

from presagio.models.baselines import BaselinePrediction, Context, Result, Target
from presagio.models.spec import TOTAL_LEAGUE_MEAN, ModelSpec

# Design 5-3 proposal values.
DEFAULT_N = 100
DEFAULT_MIN_POSTSEASON = 10
REGULAR = "regular"
POSTSEASON_TYPES = frozenset({"playin", "playoffs"})
MODEL_NAME = "league_total_t_b0"
BASE_VERSION = "1"


def total_pool(
    results: Sequence[Result],
    game_type: str,
    *,
    n: int = DEFAULT_N,
    min_postseason: int = DEFAULT_MIN_POSTSEASON,
) -> list[int]:
    """Totals of the last n results of the target's kind (regular, or postseason with the
    regular fallback below min_postseason), in the given order."""
    if game_type in POSTSEASON_TYPES:
        postseason = [r.total for r in results if r.game_type in POSTSEASON_TYPES]
        if len(postseason) >= min_postseason:
            return postseason[-n:]
    return [r.total for r in results if r.game_type == REGULAR][-n:]


def over_probability(mean: float, sd: float | None, line: float | None) -> float | None:
    """1 - Normal(mean, sd).cdf(line), None without a line or a usable sd (or when the
    normal tail is not strictly inside (0, 1), which no real line reaches)."""
    if line is None or sd is None or not sd > 0:
        return None
    p = 1.0 - NormalDist(mean, sd).cdf(line)
    return p if 0.0 < p < 1.0 else None


class LeagueTotalBaseline:
    """T-B0: total_mean = league mean total of the last n visible games of the same kind."""

    name = "T-B0 league mean total of the last 100 games (design 5-3); totals only"
    trains = False
    feature_version = None
    uses_results = True

    def __init__(self, n: int = DEFAULT_N, min_postseason: int = DEFAULT_MIN_POSTSEASON) -> None:
        """Keep the window size and the postseason-pool minimum (proposal values, 5-3)."""
        if n < 1:
            raise ValueError("n must be >= 1")
        if min_postseason < 1:
            raise ValueError("min_postseason must be >= 1")
        self.n = n
        self.min_postseason = min_postseason

    @property
    def version(self) -> str:
        """'1' with the design values, a suffix naming n and min_postseason otherwise."""
        if (self.n, self.min_postseason) == (DEFAULT_N, DEFAULT_MIN_POSTSEASON):
            return BASE_VERSION
        return f"{BASE_VERSION}-n{self.n}-minpost{self.min_postseason}"

    @property
    def spec(self) -> ModelSpec:
        """model_versions identity of this configuration."""
        return ModelSpec(
            model_name=MODEL_NAME,
            version=self.version,
            model_family=TOTAL_LEAGUE_MEAN,
            hyperparameters={
                "n": self.n,
                "min_postseason": self.min_postseason,
                "pool": "last n visible results of the target's kind in start order, across "
                "seasons: regular for regular targets; play-in + playoffs for postseason "
                "targets, regular when fewer than min_postseason postseason results",
                "total_mean": "mean total of the pool",
                "total_sd": "sample standard deviation of the pool (None below 2 games)",
                "over_prob": "1 - NormalDist(total_mean, total_sd).cdf(reference total line "
                "at as_of); None without a line or sd",
                "p_home": None,
            },
        )

    def fit(self, training: Any) -> None:
        """Nothing to learn."""

    def predict(
        self, as_of: datetime, games: Sequence[Target], context: Context
    ) -> Mapping[str, BaselinePrediction]:
        """Pool mean and sd per game kind; a game whose pool is empty is not predicted."""
        results = tuple(context.results())
        pools: dict[str, list[int]] = {}
        out: dict[str, BaselinePrediction] = {}
        for g in games:
            if g.game_type not in pools:
                pools[g.game_type] = total_pool(
                    results, g.game_type, n=self.n, min_postseason=self.min_postseason
                )
            pool = pools[g.game_type]
            if not pool:
                continue
            mean = fmean(pool)
            sd = stdev(pool) if len(pool) >= 2 else None
            if sd is not None and not sd > 0:
                sd = None  # identical totals: no spread to report (total_sd > 0 or NULL)
            market = context.market(g.game_id)
            line = market.total_line if market is not None else None
            out[g.game_id] = BaselinePrediction(
                p_home=None,
                total_mean=mean,
                over_prob=over_probability(mean, sd, line),
                total_sd=sd,
            )
        return out
