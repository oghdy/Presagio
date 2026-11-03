"""Elo rating baseline B2 of modeling design v1.2 5-2 (P-034, D-049 M-7).

A non-learning predictor of the walk-forward runner (presagio.evaluation.walkforward.Predictor,
matched structurally; this package never imports presagio.evaluation, design 9-2 Q4). Every
prediction day it rebuilds the ratings from scratch out of context.results(), the finished
games passing R1 and R2 in start order (the Cup final never reaches results(), D-049 M-3;
earlier seasons such as the 2022-23 warm-up and locked holdout seasons are visible as
information before as_of, D-055 item 1). Nothing is kept between days or sessions, so the
rating a prediction uses is a pure function of the results visible at its as_of.

Formulas (design 5-2, constants of D-049 M-7 as the starting values; the grid search is
hypothesis H-04, ticket P-037):

- expectation   E_h = 1 / (1 + 10^(-(R_h + HCA x home_adv - R_a) / 400)),
                home_adv = 0 if is_neutral else 1
- update        R_h <- R_h + K x m x (S_h - E_h),  R_a <- R_a - K x m x (S_h - E_h),
                S_h in {0, 1}, m = 1 (no margin-of-victory multiplier in v1; H-04(b))
- season break  before the first result of a season later than the previous result's,
                every team seen so far: R <- carry x R + (1 - carry) x mean (design 2-4).
                A target game of a season later than the last visible result's (a season's
                opening day) is predicted with that break already applied: the regressed
                value is the next season's starting value (2-4), and it is the same break the
                next result of that season triggers.
- initial       a team's first appearance starts at `initial` (no regression before its
                first game)

| constant | start value | source |
| K        | 20          | FiveThirtyEight, "How We Calculate NBA Elo Ratings" (2015) |
| HCA      | 100         | same (about 3.5 points) |
| carry    | 0.75        | 25% regression to the mean between seasons (secondary source) |
| mean     | 1505        | 538 value |
| initial  | 1500        | |
| mov      | None        | v1 has no margin multiplier |

p_home = E_h of the target game with its own is_neutral; the formula keeps it inside (0, 1),
so nothing is clipped. The constants are hyperparameters of the ModelSpec; a non-default K,
HCA or carry changes the version (one configuration per (model_name, version), D-055 item 3).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

from presagio.models.baselines import BaselinePrediction, Context, Result, Target
from presagio.models.spec import ELO, ModelSpec

# D-049 M-7 starting values (design 5-2 table).
DEFAULT_K = 20.0
DEFAULT_HCA = 100.0
DEFAULT_CARRY = 0.75
DEFAULT_MEAN = 1505.0
DEFAULT_INITIAL = 1500.0
MODEL_NAME = "elo_b2"
BASE_VERSION = "1"


def expected_home(r_home: float, r_away: float, *, hca: float, is_neutral: bool) -> float:
    """E_h = 1 / (1 + 10^(-(R_h + HCA x home_adv - R_a) / 400)), home_adv 0 when neutral."""
    home_adv = 0.0 if is_neutral else 1.0
    return 1.0 / (1.0 + 10.0 ** (-(r_home + hca * home_adv - r_away) / 400.0))


def regress(ratings: dict[int, float], *, carry: float, mean: float) -> None:
    """Season break in place: R <- carry x R + (1 - carry) x mean for every team seen."""
    for team in ratings:
        ratings[team] = carry * ratings[team] + (1.0 - carry) * mean


def elo_ratings(
    results: Iterable[Result],
    *,
    k: float = DEFAULT_K,
    hca: float = DEFAULT_HCA,
    carry: float = DEFAULT_CARRY,
    mean: float = DEFAULT_MEAN,
    initial: float = DEFAULT_INITIAL,
) -> dict[int, float]:
    """Ratings after processing `results` in the given order from scratch (design 5-2).

    The season regression runs before the first result whose season label is greater than
    the previous result's (string order: "2022-23" < "2023-24"). The order is the caller's
    (the runner's start order); nothing is re-sorted.
    """
    ratings: dict[int, float] = {}
    season: str | None = None
    for r in results:
        if season is not None and r.season > season:
            regress(ratings, carry=carry, mean=mean)
        season = r.season
        r_home = ratings.get(r.home_team_id, initial)
        r_away = ratings.get(r.away_team_id, initial)
        expected = expected_home(r_home, r_away, hca=hca, is_neutral=r.is_neutral)
        delta = k * (float(r.home_won) - expected)
        ratings[r.home_team_id] = r_home + delta
        ratings[r.away_team_id] = r_away - delta
    return ratings


def season_ratings(
    results: Sequence[Result],
    season: str,
    *,
    k: float = DEFAULT_K,
    hca: float = DEFAULT_HCA,
    carry: float = DEFAULT_CARRY,
    mean: float = DEFAULT_MEAN,
    initial: float = DEFAULT_INITIAL,
) -> dict[int, float]:
    """The ratings a game of `season` is predicted with: elo_ratings() of the results, plus
    the pending season break when `season` is later than the last result's (design 2-4: the
    regressed value is the next season's starting value, so a season's opening day, before
    any result of that season exists, already uses it; it is exactly the break elo_ratings()
    applies once the first result of that season arrives)."""
    ratings = elo_ratings(results, k=k, hca=hca, carry=carry, mean=mean, initial=initial)
    if results and season > results[-1].season:
        regress(ratings, carry=carry, mean=mean)
    return ratings


def _num(value: float) -> str:
    """A constant for the version string: 20 -> '20', 0.75 -> '0.75', 22.5 -> '22.5'."""
    return f"{value:g}"


class EloBaseline:
    """B2: p_home = Elo expectation from ratings rebuilt daily out of the visible results."""

    name = "B2 Elo rating (K=20, HCA=100, carry 0.75; design 5-2)"
    trains = False
    feature_version = None
    uses_results = True

    def __init__(
        self,
        k: float = DEFAULT_K,
        hca: float = DEFAULT_HCA,
        carry: float = DEFAULT_CARRY,
        mean: float = DEFAULT_MEAN,
        initial: float = DEFAULT_INITIAL,
        mov: Any = None,
    ) -> None:
        """Keep the constants (D-049 M-7 starting values; H-04 may change them in P-037)."""
        if not k > 0:
            raise ValueError(f"k must be > 0, got {k!r}")
        if not hca >= 0:
            raise ValueError(f"hca must be >= 0, got {hca!r}")
        if not 0.0 <= carry <= 1.0:
            raise ValueError(f"carry must be in [0, 1], got {carry!r}")
        if mov is not None:
            raise ValueError("a margin-of-victory multiplier is not part of v1 (H-04(b))")
        self.k = float(k)
        self.hca = float(hca)
        self.carry = float(carry)
        self.mean = float(mean)
        self.initial = float(initial)
        self.mov = None

    @property
    def version(self) -> str:
        """'1' with the M-7 constants; a suffix naming K, HCA and carry otherwise (and the
        mean or initial rating when those differ), so one (model_name, version) is one
        configuration (D-055 item 3)."""
        defaults = (DEFAULT_K, DEFAULT_HCA, DEFAULT_CARRY, DEFAULT_MEAN, DEFAULT_INITIAL)
        if (self.k, self.hca, self.carry, self.mean, self.initial) == defaults:
            return BASE_VERSION
        version = f"{BASE_VERSION}-k{_num(self.k)}-hca{_num(self.hca)}-c{_num(self.carry)}"
        if self.mean != DEFAULT_MEAN:
            version += f"-mean{_num(self.mean)}"
        if self.initial != DEFAULT_INITIAL:
            version += f"-init{_num(self.initial)}"
        return version

    @property
    def spec(self) -> ModelSpec:
        """model_versions identity of this configuration."""
        return ModelSpec(
            model_name=MODEL_NAME,
            version=self.version,
            model_family=ELO,
            hyperparameters={
                "k": self.k,
                "hca": self.hca,
                "carry": self.carry,
                "mean": self.mean,
                "initial": self.initial,
                "mov": self.mov,
                "formula": "E_h = 1 / (1 + 10^(-(R_h + hca * home_adv - R_a) / 400)), "
                "home_adv = 0 if neutral else 1; R_h += k * (S_h - E_h), R_a -= k * (S_h - E_h); "
                "season break: R = carry * R + (1 - carry) * mean; unseen team = initial; "
                "ratings rebuilt daily from the visible results (design 5-2, D-049 M-7)",
            },
        )

    def fit(self, training: Any) -> None:
        """Nothing to learn (constants are hyperparameters, tuned by H-04 if at all)."""

    def ratings(self, context: Context) -> dict[int, float]:
        """Ratings after the context's visible results, computed from scratch (without the
        pending break of a later target season; see season_ratings)."""
        return elo_ratings(
            context.results(),
            k=self.k,
            hca=self.hca,
            carry=self.carry,
            mean=self.mean,
            initial=self.initial,
        )

    def predict(
        self, as_of: datetime, games: Sequence[Target], context: Context
    ) -> Mapping[str, BaselinePrediction]:
        """E_h per target game from the day's ratings (rebuilt from scratch, one season break
        pending when the target season is later than the last result's); no total."""
        results = tuple(context.results())
        by_season: dict[str, dict[int, float]] = {}
        out: dict[str, BaselinePrediction] = {}
        for g in games:
            if g.season not in by_season:
                by_season[g.season] = season_ratings(
                    results,
                    g.season,
                    k=self.k,
                    hca=self.hca,
                    carry=self.carry,
                    mean=self.mean,
                    initial=self.initial,
                )
            ratings = by_season[g.season]
            out[g.game_id] = BaselinePrediction(
                expected_home(
                    ratings.get(g.home_team_id, self.initial),
                    ratings.get(g.away_team_id, self.initial),
                    hca=self.hca,
                    is_neutral=g.is_neutral,
                )
            )
        return out
