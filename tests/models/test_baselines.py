"""Hand-computed expectations of the win baselines B0, B1, B3 (design 5-1, P-033 criterion 1).

The baselines are pure functions of a PredictionContext; here they get small hand-built
results and markets (the runner-level leakage tests are in tests/evaluation)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from presagio.db.models.modeling import MODEL_FAMILIES
from presagio.evaluation.market import BookPrice, GameMarket, MarketProbability
from presagio.evaluation.walkforward import GameResult, PredictionContext, TargetGame
from presagio.models.baselines import (
    MIN_SEASON_GAMES,
    WIN_RATE_CLIP,
    AlwaysHomeBaseline,
    BaselinePrediction,
    MarketBaseline,
    SeasonRecord,
    SeasonWinRateBaseline,
    b0_home_probability,
    b1_home_probability,
    home_win_rate,
    previous_season,
    season_records,
)
from presagio.models.spec import ModelSpec

A, B, C, D = 1, 2, 3, 4
AS_OF = datetime(2024, 1, 15, 7, 0, tzinfo=UTC)
START = datetime(2024, 1, 16, 0, 30, tzinfo=UTC)


def result(
    n: int,
    season: str,
    home: int,
    away: int,
    home_score: int,
    away_score: int,
    neutral: bool = False,
) -> GameResult:
    """A finished game (start order follows n)."""
    start = datetime(2023, 10, 1, tzinfo=UTC) + timedelta(days=n)
    return GameResult(
        game_id=f"{n:010d}",
        season=season,
        game_type="regular",
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
        et_date=date(2024, 1, 15),
        start_time_utc=START,
        home_team_id=home,
        away_team_id=away,
        is_neutral=neutral,
    )


def context(
    results: Sequence[GameResult], markets: Mapping[str, GameMarket] | None = None
) -> PredictionContext:
    """A frozen context holding the given results and markets."""
    return PredictionContext(
        as_of=AS_OF,
        target_date=date(2024, 1, 15),
        lag=timedelta(hours=3),
        visible_results=tuple(results),
        past_schedule=(),
        markets=dict(markets or {}),
    )


def home_wins(n: int, season: str, start: int = 0) -> list[GameResult]:
    """n games the home side won (A hosts B)."""
    return [result(start + i, season, A, B, 100, 90) for i in range(n)]


def away_wins(n: int, season: str, start: int = 0) -> list[GameResult]:
    """n games the away side won (A hosts B)."""
    return [result(start + i, season, A, B, 90, 100) for i in range(n)]


def predict(predictor: Any, games: Sequence[TargetGame], ctx: PredictionContext) -> dict:
    """p_home per game id."""
    return {gid: v.p_home for gid, v in predictor.predict(AS_OF, games, ctx).items()}


# --- B0 ---------------------------------------------------------------------------------


def test_b0_uses_the_season_home_rate_once_the_sample_is_big_enough() -> None:
    """With min_season_games = 4: 3 home wins + 1 away win -> p_home = 0.75."""
    results = home_wins(3, "2023-24") + away_wins(1, "2023-24", 3)
    assert home_win_rate(results) == 0.75
    assert b0_home_probability(results, target("g", "2023-24", C, D), min_season_games=4) == 0.75
    b0 = AlwaysHomeBaseline(min_season_games=4)
    assert predict(b0, [target("g", "2023-24", C, D)], context(results)) == {"g": 0.75}


def test_b0_falls_back_to_the_previous_season_then_to_one_half() -> None:
    """Below the sample size: the previous season's full home rate (2 wins of 3 -> 2/3), and
    0.5 when no earlier season is visible."""
    previous = home_wins(2, "2022-23") + away_wins(1, "2022-23", 2)
    current = home_wins(3, "2023-24", 10)  # 3 < 4
    game = target("g", "2023-24", C, D)
    assert previous_season(previous + current, "2023-24") == "2022-23"
    assert b0_home_probability(previous + current, game, min_season_games=4) == pytest.approx(2 / 3)
    assert b0_home_probability(current, game, min_season_games=4) == 0.5
    assert b0_home_probability([], game, min_season_games=4) == 0.5
    assert previous_season(current, "2023-24") is None


def test_b0_previous_season_is_the_latest_earlier_one_not_the_target_season() -> None:
    """Two earlier seasons visible: the fallback is 2022-23 (latest before 2023-24), and the
    target season's own small sample is ignored."""
    older = away_wins(4, "2021-22")  # home rate 0
    previous = home_wins(4, "2022-23", 10)  # home rate 1
    current = away_wins(2, "2023-24", 20)
    game = target("g", "2023-24", C, D)
    assert b0_home_probability(older + previous + current, game, min_season_games=4) == 1.0


def test_b0_neutral_game_is_one_half_whatever_the_rate() -> None:
    """Design 5-1: neutral -> 0.5."""
    results = home_wins(10, "2023-24")
    assert (
        b0_home_probability(results, target("g", "2023-24", C, D, neutral=True), min_season_games=4)
        == 0.5
    )
    assert b0_home_probability(results, target("g", "2023-24", C, D), min_season_games=4) == 1.0


def test_b0_default_threshold_is_the_design_proposal() -> None:
    """100 games (design 5-1 proposal value); 99 season games still use the fallback."""
    assert MIN_SEASON_GAMES == 100
    game = target("g", "2023-24", C, D)
    assert b0_home_probability(home_wins(99, "2023-24"), game) == 0.5
    assert b0_home_probability(home_wins(100, "2023-24"), game) == 1.0
    with pytest.raises(ValueError):
        AlwaysHomeBaseline(min_season_games=0)


# --- B1 ---------------------------------------------------------------------------------


def test_b1_formula_by_hand() -> None:
    """A 3-1 (0.75) at home against B 1-3 (0.25): 0.5 + 0.5 x 0.5 = 0.75; swapped 0.25."""
    assert b1_home_probability(0.75, 0.25) == 0.75
    assert b1_home_probability(0.25, 0.75) == 0.25
    assert b1_home_probability(0.6, 0.4) == pytest.approx(0.6)


def test_b1_is_clipped_to_the_proposal_range() -> None:
    """4-0 against 0-4 would be 1.0 -> 0.95; the reverse 0.0 -> 0.05; a custom clip works."""
    assert WIN_RATE_CLIP == (0.05, 0.95)
    assert b1_home_probability(1.0, 0.0) == 0.95
    assert b1_home_probability(0.0, 1.0) == 0.05
    assert b1_home_probability(1.0, 0.0, clip=(0.1, 0.9)) == 0.9
    with pytest.raises(ValueError):
        SeasonWinRateBaseline(clip=(0.6, 0.9))


def test_b1_tie_and_first_game_are_one_half() -> None:
    """Equal rates, or either side without a game this season -> 0.5 (home)."""
    assert b1_home_probability(0.5, 0.5) == 0.5
    assert b1_home_probability(None, 0.8) == 0.5
    assert b1_home_probability(0.8, None) == 0.5
    assert SeasonRecord(0, 0).win_rate is None


def test_b1_season_records_count_both_sides_and_only_the_target_season() -> None:
    """Records from A-B games: A won 3 of 4 at home; a 2022-23 game and a game of other teams
    do not enter A's or B's 2023-24 record; postseason games do (design 2-4)."""
    results = (
        home_wins(3, "2023-24")
        + away_wins(1, "2023-24", 3)
        + home_wins(5, "2022-23", 10)
        + [result(20, "2023-24", C, D, 80, 70)]
    )
    playoff = result(30, "2023-24", B, A, 99, 98)
    playoff = GameResult(**{**playoff.__dict__, "game_type": "playoffs"})
    records = season_records(results + [playoff], "2023-24")
    assert records[A] == SeasonRecord(5, 3) and records[B] == SeasonRecord(5, 2)
    assert records[C] == SeasonRecord(1, 1) and records[D] == SeasonRecord(1, 0)
    assert season_records(results, "2024-25") == {}


def test_b1_predict_by_hand() -> None:
    """A 3-1 vs B 1-3: A at home 0.75, B at home 0.25; C (no game) at home vs A -> 0.5."""
    results = home_wins(3, "2023-24") + away_wins(1, "2023-24", 3)
    b1 = SeasonWinRateBaseline()
    games = [
        target("ab", "2023-24", A, B),
        target("ba", "2023-24", B, A),
        target("ca", "2023-24", C, A),
        target("ab25", "2024-25", A, B),  # new season: no record yet
    ]
    assert predict(b1, games, context(results)) == {"ab": 0.75, "ba": 0.25, "ca": 0.5, "ab25": 0.5}


# --- B3 ---------------------------------------------------------------------------------


def market(p_home: float | None) -> GameMarket:
    """A prediction-time market with (or without) a consensus home probability."""
    if p_home is None:
        return GameMarket()
    captured = AS_OF - timedelta(hours=9)
    home = MarketProbability(
        p_home=p_home,
        source="consensus",
        bookmakers=("betmgm", "draftkings", "fanduel"),
        captured_at=captured,
        oldest_captured_at=captured,
        snapshot_ids=(1, 2, 3, 4, 5, 6),
        method="proportional",
    )
    book = BookPrice(
        bookmaker_key="draftkings",
        market_key="h2h",
        captured_at=captured,
        prices={"home": Decimal("1.90"), "away": Decimal("2.00")},
        fair={"home": p_home, "away": 1 - p_home},
        point=None,
        snapshot_ids=(1, 2),
        method="proportional",
    )
    return GameMarket(h2h_books={"draftkings": book}, home=home, h2h_reference=book)


def test_b3_returns_the_market_probability_and_skips_games_without_one() -> None:
    """Three targets: a consensus market (0.4615), an empty market, no market at all."""
    games = [
        target("m", "2026-27", A, B),
        target("e", "2026-27", C, D),
        target("n", "2026-27", B, C),
    ]
    ctx = context([], {"m": market(0.4615), "e": market(None)})
    out = MarketBaseline().predict(AS_OF, games, ctx)
    assert set(out) == {"m"}
    assert out["m"] == BaselinePrediction(p_home=0.4615)
    assert out["m"].total_mean is None and out["m"].over_prob is None


def test_b3_ignores_results_entirely() -> None:
    """The same markets with very different results give the same predictions."""
    games = [target("m", "2026-27", A, B)]
    markets = {"m": market(0.7)}
    assert predict(MarketBaseline(), games, context([], markets)) == predict(
        MarketBaseline(), games, context(home_wins(50, "2026-27"), markets)
    )
    assert MarketBaseline.uses_results is False


# --- specs ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("predictor", "name", "family"),
    [
        (AlwaysHomeBaseline(), "always_home_b0", "baseline_home"),
        (SeasonWinRateBaseline(), "season_win_rate_b1", "baseline_season_wr"),
        (MarketBaseline(), "market_b3", "market"),
    ],
)
def test_specs_name_a_known_family_and_carry_the_proposal_values(
    predictor: Any, name: str, family: str
) -> None:
    """model_versions identity: a schema v1 family, version 1, no feature rows, no tuning."""
    spec = predictor.spec
    assert isinstance(spec, ModelSpec)
    assert (spec.model_name, spec.version, spec.model_family) == (name, "1", family)
    assert spec.model_family in MODEL_FAMILIES
    assert spec.feature_version is None and spec.tuning_data is None
    assert predictor.trains is False and predictor.feature_version is None
    assert isinstance(spec.hyperparameters, Mapping)


def test_hyperparameters_hold_the_proposal_values() -> None:
    """B0 min_season_games 100, B1 clip [0.05, 0.95]."""
    assert AlwaysHomeBaseline().spec.hyperparameters["min_season_games"] == 100
    assert AlwaysHomeBaseline(50).spec.hyperparameters["min_season_games"] == 50
    assert SeasonWinRateBaseline().spec.hyperparameters["clip"] == [0.05, 0.95]
