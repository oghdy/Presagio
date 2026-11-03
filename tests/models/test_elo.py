"""Hand-computed expectations of the Elo baseline B2 (design 5-2, D-049 M-7; P-034
criterion 6 (a)..(f)). No database: small hand-built results in a frozen context."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

import pytest

from presagio.db.models.modeling import MODEL_FAMILIES
from presagio.evaluation.walkforward import GameResult, PredictionContext, TargetGame
from presagio.models.baselines import BaselinePrediction
from presagio.models.elo import (
    DEFAULT_CARRY,
    DEFAULT_HCA,
    DEFAULT_K,
    EloBaseline,
    elo_ratings,
    expected_home,
    regress,
)
from presagio.models.spec import ELO, ModelSpec

A, B, C, D = 1, 2, 3, 4
AS_OF = datetime(2023, 10, 25, 7, 0, tzinfo=UTC)
START = datetime(2023, 10, 26, 0, 30, tzinfo=UTC)
# Design 5-2 by hand, all teams at 1500, HCA 100, K 20:
#   E_h = 1 / (1 + 10^(-100/400)) = 0.640065..., home win -> +-20 x 0.359935 = +-7.1987
E_HOME_1500 = 0.640065
R_HOME_AFTER = 1507.1987
R_AWAY_AFTER = 1492.8013


def result(
    n: int,
    season: str,
    home: int,
    away: int,
    home_score: int,
    away_score: int,
    neutral: bool = False,
    game_type: str = "regular",
) -> GameResult:
    """A finished game (start order follows n)."""
    start = datetime(2022, 10, 1, tzinfo=UTC) + timedelta(days=n)
    return GameResult(
        game_id=f"{n:010d}",
        season=season,
        game_type=game_type,
        et_date=start.date(),
        start_time_utc=start,
        available_at=start + timedelta(hours=3),
        home_team_id=home,
        away_team_id=away,
        home_score=home_score,
        away_score=away_score,
        is_neutral=neutral,
    )


def target(game_id: str, season: str, home: int, away: int, neutral: bool = False) -> TargetGame:
    """A game to predict."""
    return TargetGame(
        game_id=game_id,
        season=season,
        game_type="regular",
        et_date=date(2023, 10, 25),
        start_time_utc=START,
        home_team_id=home,
        away_team_id=away,
        is_neutral=neutral,
    )


def context(results: Sequence[GameResult]) -> PredictionContext:
    """A frozen context holding the given results and no market."""
    return PredictionContext(
        as_of=AS_OF,
        target_date=date(2023, 10, 25),
        lag=timedelta(hours=3),
        visible_results=tuple(results),
        past_schedule=(),
        markets={},
    )


def p_home(predictor: EloBaseline, games: Sequence[TargetGame], ctx: PredictionContext) -> dict:
    """p_home per game id."""
    return {gid: v.p_home for gid, v in predictor.predict(AS_OF, games, ctx).items()}


# --- (a) one game ----------------------------------------------------------------------


def test_a_one_home_win_from_1500_by_hand() -> None:
    """E_h = 0.6401, then R_h = 1507.20 and R_a = 1492.80 (2 decimals)."""
    assert round(expected_home(1500, 1500, hca=100, is_neutral=False), 4) == 0.6401
    ratings = elo_ratings([result(0, "2023-24", A, B, 100, 90)])
    assert round(ratings[A], 2) == 1507.20 and round(ratings[B], 2) == 1492.80
    assert ratings[A] - 1500 == pytest.approx(1500 - ratings[B])


def test_a_the_same_game_neutral_is_a_coin_flip_and_moves_ten_points() -> None:
    """Neutral: E_h = 0.5, +-10.00 after a home win."""
    assert expected_home(1500, 1500, hca=100, is_neutral=True) == 0.5
    ratings = elo_ratings([result(0, "2023-24", A, B, 100, 90, neutral=True)])
    assert (round(ratings[A], 2), round(ratings[B], 2)) == (1510.00, 1490.00)


def test_a_the_prediction_is_the_expectation_from_the_current_ratings() -> None:
    """Before any result every game is 0.6401 (home) or 0.5 (neutral); after A beat B at
    home, A hosting B again is E_h from 1507.1987 vs 1492.8013 and B hosting A is the
    mirror, each with its own HCA."""
    elo = EloBaseline()
    games = [target("h", "2023-24", A, B), target("n", "2023-24", A, B, neutral=True)]
    assert {k: round(v, 4) for k, v in p_home(elo, games, context([])).items()} == {
        "h": 0.6401,
        "n": 0.5,
    }
    ctx = context([result(0, "2023-24", A, B, 100, 90)])
    out = p_home(elo, [target("ab", "2023-24", A, B), target("ba", "2023-24", B, A)], ctx)
    assert out["ab"] == pytest.approx(
        1 / (1 + 10 ** (-(R_HOME_AFTER + 100 - R_AWAY_AFTER) / 400)), abs=1e-6
    )
    assert out["ba"] == pytest.approx(
        1 / (1 + 10 ** (-(R_AWAY_AFTER + 100 - R_HOME_AFTER) / 400)), abs=1e-6
    )
    # HCA (100) outweighs the 14.4-point rating gap: each host is the favourite, A more so.
    assert 0.5 < out["ba"] < 0.6401 < out["ab"] < 1


# --- (b) two games ---------------------------------------------------------------------


def test_b_two_games_in_a_row_by_hand() -> None:
    """A beats B at home (A 1507.1987, B 1492.8013); then B hosts A and A wins:
    E_h(B) = 1 / (1 + 10^(-(1492.8013 + 100 - 1507.1987) / 400)) = 0.620758,
    B <- 1492.8013 - 20 x 0.620758 = 1480.3861, A <- 1507.1987 + 12.4152 = 1519.6139."""
    games = [result(0, "2023-24", A, B, 100, 90), result(1, "2023-24", B, A, 90, 100)]
    e2 = 1 / (1 + 10 ** (-(R_AWAY_AFTER + 100 - R_HOME_AFTER) / 400))
    assert round(e2, 6) == 0.620758
    ratings = elo_ratings(games)
    assert round(ratings[B], 4) == round(R_AWAY_AFTER - 20 * e2, 4) == 1480.3861
    assert round(ratings[A], 4) == round(R_HOME_AFTER + 20 * e2, 4) == 1519.6139
    # The order matters (sequential update): the two games swapped give other ratings.
    assert elo_ratings(list(reversed(games)))[A] != ratings[A]


# --- (c) season boundary ---------------------------------------------------------------


def test_c_regression_to_the_mean_by_hand() -> None:
    """1600 -> 0.75 x 1600 + 0.25 x 1505 = 1576.25; 1500 -> 1501.25."""
    ratings = {A: 1600.0, B: 1500.0}
    regress(ratings, carry=0.75, mean=1505.0)
    assert ratings == {A: 1576.25, B: 1501.25}


def test_c_the_break_runs_before_the_first_game_of_the_next_season_only_for_seen_teams() -> None:
    """A-B in 2022-23, then C-D in 2023-24: A and B regress before C-D, C and D start at 1500
    untouched (no regression before a team's first game)."""
    ratings = elo_ratings(
        [result(0, "2022-23", A, B, 100, 90), result(1, "2023-24", C, D, 100, 90)]
    )
    assert round(ratings[A], 4) == round(0.75 * R_HOME_AFTER + 0.25 * 1505, 4) == 1506.6490
    assert round(ratings[B], 4) == round(0.75 * R_AWAY_AFTER + 0.25 * 1505, 4) == 1495.8510
    assert (round(ratings[C], 4), round(ratings[D], 4)) == (1507.1987, 1492.8013)
    same_season = elo_ratings(
        [result(0, "2023-24", A, B, 100, 90), result(1, "2023-24", C, D, 100, 90)]
    )
    assert (round(same_season[A], 4), round(same_season[B], 4)) == (1507.1987, 1492.8013)


def test_c_two_boundaries_regress_twice() -> None:
    """2022-23 -> 2023-24 -> 2024-25 with A-B played once per season: A's rating before the
    2024-25 game is the twice-regressed, twice-updated value computed by hand."""
    results = [
        result(0, "2022-23", A, B, 100, 90),
        result(1, "2023-24", A, B, 100, 90),
        result(2, "2024-25", C, D, 100, 90),
    ]
    a1 = 0.75 * R_HOME_AFTER + 0.25 * 1505  # after the first break
    b1 = 0.75 * R_AWAY_AFTER + 0.25 * 1505
    e = 1 / (1 + 10 ** (-(a1 + 100 - b1) / 400))
    a2, b2 = a1 + 20 * (1 - e), b1 - 20 * (1 - e)  # after the 2023-24 game
    a3, b3 = 0.75 * a2 + 0.25 * 1505, 0.75 * b2 + 0.25 * 1505  # second break
    ratings = elo_ratings(results)
    assert ratings[A] == pytest.approx(a3, abs=1e-6) and ratings[B] == pytest.approx(b3, abs=1e-6)
    once = elo_ratings(results[:2])
    assert once[A] == pytest.approx(a2, abs=1e-6)


def test_c_the_break_is_the_season_label_of_the_result_not_the_date() -> None:
    """Two results of one season far apart in time never trigger the break; a label that is
    smaller than the previous one (never in start order) does not trigger it either."""
    far_apart = [result(0, "2023-24", A, B, 100, 90), result(300, "2023-24", C, D, 100, 90)]
    assert round(elo_ratings(far_apart)[A], 4) == 1507.1987
    backwards = [result(0, "2023-24", A, B, 100, 90), result(1, "2022-23", C, D, 100, 90)]
    assert round(elo_ratings(backwards)[A], 4) == 1507.1987


# --- (d) warm-up -----------------------------------------------------------------------


def test_d_earlier_season_results_warm_the_first_prediction_up() -> None:
    """With 2022-23 results the first 2023-24 prediction differs from the 1500 start; without
    them it is the HCA-only value (0.6401 home, 0.5 neutral)."""
    elo = EloBaseline()
    warmup = [result(n, "2022-23", A, B, 100, 90) for n in range(5)]
    games = [target("h", "2023-24", A, B), target("n", "2023-24", A, B, neutral=True)]
    warmed = p_home(elo, games, context(warmup))
    cold = p_home(elo, games, context([]))
    assert {k: round(v, 4) for k, v in cold.items()} == {"h": 0.6401, "n": 0.5}
    assert warmed["h"] > cold["h"] and warmed["n"] > cold["n"]
    # The warm start is the regressed 2022-23 rating (checked by hand on the neutral game).
    ratings = elo_ratings(warmup)
    a, b = 0.75 * ratings[A] + 0.25 * 1505, 0.75 * ratings[B] + 0.25 * 1505
    assert warmed["n"] == pytest.approx(1 / (1 + 10 ** (-(a - b) / 400)), abs=1e-9)


def test_d_the_regression_applies_at_the_first_prediction_of_a_new_season() -> None:
    """Design 2-4: the first prediction of 2023-24 after a 2022-23 warm-up uses the regressed
    rating, which elo_ratings() can only know from the target's season, so the predictor
    applies the break for a target season later than the last visible result's season."""
    elo = EloBaseline()
    warmup = [result(n, "2022-23", A, B, 100, 90) for n in range(5)]
    ratings = elo_ratings(warmup)
    a, b = 0.75 * ratings[A] + 0.25 * 1505, 0.75 * ratings[B] + 0.25 * 1505
    out = p_home(elo, [target("n", "2023-24", A, B, neutral=True)], context(warmup))
    assert out["n"] == pytest.approx(1 / (1 + 10 ** (-(a - b) / 400)), abs=1e-9)
    unregressed = 1 / (1 + 10 ** (-(ratings[A] - ratings[B]) / 400))
    assert out["n"] != pytest.approx(unregressed, abs=1e-9)
    # A target of the same season as the last result is not regressed.
    same = p_home(elo, [target("n", "2022-23", A, B, neutral=True)], context(warmup))
    assert same["n"] == pytest.approx(unregressed, abs=1e-9)


# --- (e) determinism -------------------------------------------------------------------


def test_e_same_input_twice_gives_the_same_output_and_equals_a_fresh_computation() -> None:
    """Two predict() calls on one instance and a fresh instance agree exactly; the ratings a
    predictor uses are the from-scratch elo_ratings() of the same results (no state)."""
    results = [
        result(n, "2023-24", [A, C][n % 2], [B, D][n % 2], 100 + n % 3, 95) for n in range(30)
    ]
    games = [target("ab", "2023-24", A, B), target("cd", "2023-24", C, D)]
    elo = EloBaseline()
    first = elo.predict(AS_OF, games, context(results))
    second = elo.predict(AS_OF, games, context(results))
    fresh = EloBaseline().predict(AS_OF, games, context(results))
    assert first == second == fresh
    assert elo.ratings(context(results)) == elo_ratings(results)
    # A call on other results leaves nothing behind.
    elo.predict(AS_OF, games, context(results[:10]))
    assert elo.predict(AS_OF, games, context(results)) == first


# --- (f) version and hyperparameters ---------------------------------------------------


def test_f_default_constants_are_version_1_with_every_hyperparameter_recorded() -> None:
    """model_name elo_b2, family elo (in MODEL_FAMILIES), version '1', the six constants."""
    spec = EloBaseline().spec
    assert isinstance(spec, ModelSpec)
    assert (spec.model_name, spec.version, spec.model_family) == ("elo_b2", "1", ELO)
    assert ELO in MODEL_FAMILIES
    assert spec.feature_version is None and spec.tuning_data is None
    hp = dict(spec.hyperparameters)
    assert {k: hp[k] for k in ("k", "hca", "carry", "mean", "initial", "mov")} == {
        "k": 20.0,
        "hca": 100.0,
        "carry": 0.75,
        "mean": 1505.0,
        "initial": 1500.0,
        "mov": None,
    }
    assert "E_h" in hp["formula"]
    assert (DEFAULT_K, DEFAULT_HCA, DEFAULT_CARRY) == (20, 100, 0.75)
    elo = EloBaseline()
    assert elo.trains is False and elo.feature_version is None and elo.uses_results is True


@pytest.mark.parametrize(
    ("kwargs", "version"),
    [
        ({"k": 25}, "1-k25-hca100-c0.75"),
        ({"hca": 75}, "1-k20-hca75-c0.75"),
        ({"carry": 0.9}, "1-k20-hca100-c0.9"),
        ({"k": 15, "hca": 125, "carry": 0.6}, "1-k15-hca125-c0.6"),
        ({"mean": 1500}, "1-k20-hca100-c0.75-mean1500"),
        ({"initial": 1400}, "1-k20-hca100-c0.75-init1400"),
    ],
)
def test_f_other_constants_change_the_version_and_are_recorded(kwargs: dict, version: str) -> None:
    """D-055 item 3: one (model_name, version) is one configuration."""
    spec = EloBaseline(**kwargs).spec
    assert spec.version == version and spec.model_name == "elo_b2"
    for key, value in kwargs.items():
        assert spec.hyperparameters[key] == value


def test_f_the_constants_change_the_prediction() -> None:
    """K, HCA and carry each move a prediction (the hyperparameters are not decorative)."""
    results = [result(0, "2022-23", A, B, 100, 90), result(1, "2023-24", A, B, 100, 90)]
    game = [target("g", "2023-24", A, B)]
    base = p_home(EloBaseline(), game, context(results))["g"]
    assert p_home(EloBaseline(k=30), game, context(results))["g"] > base
    assert p_home(EloBaseline(hca=0), game, context(results))["g"] < base
    assert p_home(EloBaseline(carry=0.5), game, context(results))["g"] != base
    assert p_home(EloBaseline(initial=1400), game, context(results))["g"] == pytest.approx(base)
    # Regression moves every seen team by the same amount, so the mean only shows against
    # an unseen team (C, still at the initial rating).
    new_team = [target("g", "2023-24", A, C)]
    assert p_home(EloBaseline(mean=1600), game, context(results))["g"] == pytest.approx(base)
    assert p_home(EloBaseline(mean=1600), new_team, context(results))["g"] != pytest.approx(
        p_home(EloBaseline(), new_team, context(results))["g"]
    )


@pytest.mark.parametrize(
    "kwargs", [{"k": 0}, {"k": -1}, {"hca": -1}, {"carry": 1.5}, {"carry": -0.1}, {"mov": 1.0}]
)
def test_f_impossible_constants_are_refused(kwargs: dict) -> None:
    """K <= 0, a negative HCA, carry outside [0, 1] and any MOV multiplier are errors."""
    with pytest.raises(ValueError):
        EloBaseline(**kwargs)


def test_prediction_carries_no_total() -> None:
    """B2 is a win model: total_mean, total_sd and over_prob are None."""
    (value,) = EloBaseline().predict(AS_OF, [target("g", "2023-24", A, B)], context([])).values()
    assert isinstance(value, BaselinePrediction)
    assert (value.total_mean, value.total_sd, value.over_prob) == (None, None, None)
    assert 0 < value.p_home < 1
