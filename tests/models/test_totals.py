"""Hand-computed expectations of the league-total baseline T-B0 (design 5-3; P-034
criterion 6 (g)). No database: hand-built results and markets in a frozen context."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from statistics import NormalDist

import pytest

from presagio.db.models.modeling import MODEL_FAMILIES
from presagio.evaluation.market import BookPrice, GameMarket
from presagio.evaluation.walkforward import GameResult, PredictionContext, TargetGame
from presagio.models.baselines import BaselinePrediction
from presagio.models.spec import TOTAL_LEAGUE_MEAN, ModelSpec
from presagio.models.totals import LeagueTotalBaseline, over_probability, total_pool

A, B, C, D = 1, 2, 3, 4
AS_OF = datetime(2024, 1, 15, 7, 0, tzinfo=UTC)
START = datetime(2024, 1, 16, 0, 30, tzinfo=UTC)
# 150 regular results; the last 100 have totals 200..299: mean 249.5, sample sd
# sqrt(100 x (100^2 - 1) / 12 / 99) = 29.0115. The first 50 (totals 100..149) never count.
POOL_MEAN = 249.5
POOL_SD = (100 * (100**2 - 1) / 12 / 99) ** 0.5


def result(n: int, total: int, game_type: str = "regular", season: str = "2023-24") -> GameResult:
    """A finished game with the given total (start order follows n)."""
    start = datetime(2023, 10, 1, tzinfo=UTC) + timedelta(days=n)
    return GameResult(
        game_id=f"{n:010d}",
        season=season,
        game_type=game_type,
        et_date=start.date(),
        start_time_utc=start,
        available_at=start + timedelta(hours=3),
        home_team_id=A if n % 2 else C,
        away_team_id=B if n % 2 else D,
        home_score=total - 90,
        away_score=90,
        is_neutral=False,
    )


def regular_results() -> list[GameResult]:
    """50 results with totals 100..149, then 100 with totals 200..299."""
    return [result(n, 100 + n) for n in range(50)] + [result(50 + n, 200 + n) for n in range(100)]


def target(game_id: str, game_type: str = "regular") -> TargetGame:
    """A game to predict."""
    return TargetGame(
        game_id=game_id,
        season="2023-24",
        game_type=game_type,
        et_date=date(2024, 1, 15),
        start_time_utc=START,
        home_team_id=A,
        away_team_id=B,
        is_neutral=False,
    )


def totals_market(line: float | None) -> GameMarket:
    """A prediction-time market with (or without) a DraftKings total line."""
    if line is None:
        return GameMarket()
    book = BookPrice(
        bookmaker_key="draftkings",
        market_key="totals",
        captured_at=AS_OF - timedelta(hours=9),
        prices={"over": Decimal("1.91"), "under": Decimal("1.91")},
        fair={"over": 0.5, "under": 0.5},
        point=Decimal(str(line)),
        snapshot_ids=(1, 2),
        method="proportional",
    )
    return GameMarket(totals_books={"draftkings": book}, totals_reference=book)


def context(results: Sequence[GameResult], markets: dict | None = None) -> PredictionContext:
    """A frozen context holding the given results and markets."""
    return PredictionContext(
        as_of=AS_OF,
        target_date=date(2024, 1, 15),
        lag=timedelta(hours=3),
        visible_results=tuple(results),
        past_schedule=(),
        markets=dict(markets or {}),
    )


def predict_one(
    results: Sequence[GameResult], game: TargetGame, line: float | None = None
) -> BaselinePrediction | None:
    """T-B0's prediction of one game (None when it is not predicted)."""
    ctx = context(results, {game.game_id: totals_market(line)} if line is not None else None)
    return LeagueTotalBaseline().predict(AS_OF, [game], ctx).get(game.game_id)


def test_g_last_100_regular_results_by_hand() -> None:
    """Mean 249.5 and sample sd 29.0115 of the totals 200..299; p_home None; no line -> no
    over probability; the sd is total_sd."""
    value = predict_one(regular_results(), target("g"))
    assert value is not None and value.p_home is None
    assert value.total_mean == POOL_MEAN
    assert value.total_sd == pytest.approx(POOL_SD) and round(value.total_sd, 4) == 29.0115
    assert value.over_prob is None
    assert total_pool(regular_results(), "regular") == list(range(200, 300))


def test_g_the_first_50_results_do_not_count() -> None:
    """Changing (or removing) the 50 oldest results leaves the prediction unchanged; changing
    one of the last 100 changes it (the window is the last n, not the whole history)."""
    base = predict_one(regular_results(), target("g"))
    changed = [result(n, 999) for n in range(50)] + regular_results()[50:]
    assert predict_one(changed, target("g")) == base
    assert predict_one(regular_results()[50:], target("g")) == base
    inside = regular_results()[:-1] + [result(149, 999)]
    assert predict_one(inside, target("g")) != base


def test_g_the_window_is_counted_in_the_given_order_across_seasons() -> None:
    """The pool is the last n in the given (start) order whatever the season labels: 2022-23
    and 2023-24 results mix (a league level, not a within-season rolling window)."""
    results = [result(n, 100 + n, season="2022-23") for n in range(50)] + [
        result(50 + n, 200 + n) for n in range(100)
    ]
    assert predict_one(results, target("g")).total_mean == POOL_MEAN
    mixed = [result(n, 200 + n, season="2022-23") for n in range(60)] + [
        result(60 + n, 260 + n) for n in range(40)
    ]
    assert predict_one(mixed, target("g")).total_mean == POOL_MEAN


def test_g_postseason_targets_use_the_postseason_pool_from_ten_results() -> None:
    """10 postseason results (totals 300..309, mean 304.5): a play-in or playoff target uses
    them; with 9 the regular pool (249.5) is used instead; a regular target never uses the
    postseason pool."""
    regular = regular_results()
    post10 = [result(200 + n, 300 + n, "playoffs" if n % 2 else "playin") for n in range(10)]
    for kind in ("playin", "playoffs"):
        value = predict_one(regular + post10, target("p", kind))
        assert value.total_mean == 304.5 and value.total_sd == pytest.approx(3.02765, abs=1e-5)
    assert predict_one(regular + post10[:9], target("p", "playoffs")).total_mean == POOL_MEAN
    assert predict_one(regular + post10, target("r")).total_mean == POOL_MEAN
    assert total_pool(regular + post10, "playin", min_postseason=11) == list(range(200, 300))


def test_g_an_empty_pool_means_no_prediction() -> None:
    """No visible result: nothing is predicted (no exception). A postseason target with no
    result of either kind is not predicted either; a regular target with only postseason
    results is not predicted (the regular pool is empty)."""
    assert LeagueTotalBaseline().predict(AS_OF, [target("g")], context([])) == {}
    assert predict_one([], target("p", "playin")) is None
    post = [result(n, 300, "playoffs") for n in range(12)]
    assert predict_one(post, target("g")) is None
    assert predict_one(post, target("p", "playoffs")).total_mean == 300


def test_g_over_probability_by_hand() -> None:
    """Line = mean -> 0.5; line = mean + 1 sd -> 0.1587; no line -> None."""
    results = regular_results()
    assert predict_one(results, target("g"), POOL_MEAN).over_prob == pytest.approx(0.5)
    one_sd = predict_one(results, target("g"), POOL_MEAN + POOL_SD).over_prob
    assert round(one_sd, 4) == 0.1587
    assert one_sd == pytest.approx(1 - NormalDist(0, 1).cdf(1))
    assert predict_one(results, target("g"), None).over_prob is None
    assert over_probability(249.5, 29.0, None) is None
    assert over_probability(249.5, 29.0, 249.5) == 0.5
    assert over_probability(249.5, 29.0, 220.5) == pytest.approx(
        1 - NormalDist(249.5, 29.0).cdf(220.5)
    )


def test_g_one_sample_or_no_spread_gives_no_sd_and_no_over_probability() -> None:
    """A single result: total_mean = its total, total_sd None, over_prob None even with a
    line. Identical totals: sd 0 -> total_sd None, over_prob None."""
    value = predict_one([result(0, 230)], target("g"), 225.5)
    assert (value.total_mean, value.total_sd, value.over_prob) == (230, None, None)
    flat = predict_one([result(n, 230) for n in range(5)], target("g"), 225.5)
    assert (flat.total_mean, flat.total_sd, flat.over_prob) == (230, None, None)
    assert over_probability(230, 0.0, 225.5) is None and over_probability(230, None, 225.5) is None


def test_g_the_line_is_the_market_of_the_target_game_only() -> None:
    """Two targets, one with a line: only that one gets an over probability; the means are
    the same (one pool per game kind)."""
    ctx = context(regular_results(), {"a": totals_market(POOL_MEAN)})
    out = LeagueTotalBaseline().predict(AS_OF, [target("a"), target("b")], ctx)
    assert out["a"].over_prob == pytest.approx(0.5) and out["b"].over_prob is None
    assert out["a"].total_mean == out["b"].total_mean == POOL_MEAN


def test_spec_and_version() -> None:
    """model_name league_total_t_b0, family total_league_mean (in MODEL_FAMILIES), version
    '1' with n = 100 and min_postseason = 10, a suffix otherwise; totals only."""
    spec = LeagueTotalBaseline().spec
    assert isinstance(spec, ModelSpec)
    assert (spec.model_name, spec.version, spec.model_family) == (
        "league_total_t_b0",
        "1",
        TOTAL_LEAGUE_MEAN,
    )
    assert TOTAL_LEAGUE_MEAN in MODEL_FAMILIES
    assert spec.feature_version is None and spec.tuning_data is None
    assert spec.hyperparameters["n"] == 100 and spec.hyperparameters["min_postseason"] == 10
    assert spec.hyperparameters["p_home"] is None
    assert LeagueTotalBaseline(n=50).spec.version == "1-n50-minpost10"
    assert LeagueTotalBaseline(min_postseason=5).spec.version == "1-n100-minpost5"
    model = LeagueTotalBaseline()
    assert model.trains is False and model.feature_version is None and model.uses_results
    with pytest.raises(ValueError):
        LeagueTotalBaseline(n=0)
    with pytest.raises(ValueError):
        LeagueTotalBaseline(min_postseason=0)


def test_a_smaller_window_is_honoured() -> None:
    """n = 10: the last ten totals 290..299 -> mean 294.5."""
    out = LeagueTotalBaseline(n=10).predict(AS_OF, [target("g")], context(regular_results()))
    assert out["g"].total_mean == 294.5
