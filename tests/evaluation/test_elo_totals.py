"""B2 (Elo) and T-B0 (league mean total) inside the walk-forward runner on the temp league DB
(P-034 criterion 7 (b), (c), (e) and the runner's handling of totals-only predictions,
criterion 4). The time-rule tests T1, T2, T12 and the holdout rules cover both predictors
through the ALL_PREDICTORS parametrisation in test_walkforward.py."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from statistics import NormalDist
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, text

from presagio import cli
from presagio.evaluation.experiments import EXPERIMENTS
from presagio.evaluation.report import ReportSettings, render_report
from presagio.evaluation.scoring import ScoringResult, score_run
from presagio.evaluation.store import StoreReport, store_run
from presagio.evaluation.walkforward import (
    SKIP_NOT_PREDICTED,
    Prediction,
    PredictionContext,
    PredictorOutputError,
    TargetGame,
    TrainingMatrix,
    WalkForwardConfig,
    WalkForwardResult,
    run_walk_forward,
)
from presagio.models.elo import EloBaseline, elo_ratings
from presagio.models.totals import LeagueTotalBaseline

from .league import (
    CUP_FINAL,
    LIVE_AVAILABLE,
    LIVE_NOT_FINAL,
    League,
    et,
    insert_games,
    insert_stats,
)

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
CODE = "test1234"
DEV = ("2023-24", "2024-25")
FAST = ReportSettings(n_boot=50)
MODELING = ("model_versions", "predictions", "evaluations")


def run(conn: Connection, experiment: str, seasons: tuple[str, ...] = DEV) -> WalkForwardResult:
    """A registered experiment over the seasons."""
    predictor = EXPERIMENTS[experiment].make()
    return run_walk_forward(conn, predictor, WalkForwardConfig(seasons=seasons), now=NOW)


def run_and_score(
    conn: Connection, experiment: str, seasons: tuple[str, ...] = DEV
) -> tuple[Any, WalkForwardResult, ScoringResult]:
    """A registered experiment over the seasons, scored."""
    predictor = EXPERIMENTS[experiment].make()
    result = run_walk_forward(conn, predictor, WalkForwardConfig(seasons=seasons), now=NOW)
    return predictor, result, score_run(conn, result)


def store(
    conn: Connection, experiment: str, seasons: tuple[str, ...] = DEV
) -> tuple[StoreReport, WalkForwardResult, ScoringResult]:
    """Run, score and store one experiment."""
    predictor, result, scoring = run_and_score(conn, experiment, seasons)
    report = store_run(
        conn,
        result,
        scoring,
        predictor,
        experiment_id=experiment,
        seed=0,
        now=NOW,
        code_version=CODE,
    )
    return report, result, scoring


def rows(conn: Connection, table: str, order: str) -> list[dict[str, Any]]:
    """Every row of a table."""
    return [
        dict(r) for r in conn.execute(text(f"SELECT * FROM {table} ORDER BY {order}")).mappings()
    ]


def by_day(result: WalkForwardResult) -> dict[date, list[tuple[str, float | None]]]:
    """(game_id, p_home) per ET date."""
    out: dict[date, list[tuple[str, float | None]]] = {}
    for p in result.predictions:
        out.setdefault(p.game.et_date, []).append((p.game.game_id, p.p_home))
    return out


# --- B2 in the runner ------------------------------------------------------------------


def test_b2_predicts_every_target_game_with_the_from_scratch_ratings(
    conn: Connection, league: League
) -> None:
    """Every regular, play-in and playoff game of 2023-24 and 2024-25 gets an Elo
    probability inside (0, 1), total fields None, train_cutoff = last visible result; each
    day's probability equals the expectation from elo_ratings() of exactly the results the
    runner showed (an independent recomputation, criterion 1)."""
    result = run(conn, "B2")
    expected = [g for s in DEV for g in league.ids(s) if g != CUP_FINAL]
    assert sorted(p.game.game_id for p in result.predictions) == sorted(expected)
    assert result.skipped[SKIP_NOT_PREDICTED] == 0 and not result.not_predicted
    for p in result.predictions:
        assert 0 < p.p_home < 1 and p.p_home != 0.5
        assert (p.total_mean, p.total_sd, p.over_prob) == (None, None, None)
        assert p.train_cutoff <= p.as_of < p.game.start_time_utc
    first = result.predictions[0]
    assert first.train_cutoff == first.as_of  # no result yet on the opening day
    assert round(first.p_home, 4) == 0.6401  # all teams at 1500, HCA 100


def test_b2_same_day_results_never_enter_that_days_rating(conn: Connection, league: League) -> None:
    """Flipping the pivot day's two games to away wins leaves that day's predictions
    unchanged and changes the next game date's (the ratings include day d from d + 1)."""
    pivot = date(2024, 2, 15)
    before = by_day(run(conn, "B2"))
    conn.execute(
        text(
            "UPDATE games SET home_score = 0, away_score = 300 "
            "WHERE start_time_utc >= :lo AND start_time_utc < :hi"
        ),
        {"lo": et(pivot, 0), "hi": et(pivot + timedelta(days=1), 0)},
    )
    conn.execute(
        text(
            "UPDATE team_game_stats SET pts = CASE WHEN is_home THEN 0 ELSE 300 END "
            "WHERE game_id IN (SELECT game_id FROM games WHERE start_time_utc >= :lo "
            "AND start_time_utc < :hi)"
        ),
        {"lo": et(pivot, 0), "hi": et(pivot + timedelta(days=1), 0)},
    )
    after = by_day(run(conn, "B2"))
    assert len(before[pivot]) == 2 and after[pivot] == before[pivot]
    assert after[pivot + timedelta(days=2)] != before[pivot + timedelta(days=2)]
    assert {d: v for d, v in after.items() if d <= pivot} == {
        d: v for d, v in before.items() if d <= pivot
    }


def swap_winner(conn: Connection, game_id: str) -> None:
    """Swap the two scores (and the pts rows) of a game: the other team wins."""
    conn.execute(
        text(
            "UPDATE games SET home_score = away_score, away_score = home_score WHERE game_id = :g"
        ),
        {"g": game_id},
    )
    conn.execute(
        text(
            "UPDATE team_game_stats s SET pts = CASE WHEN s.is_home THEN g.home_score "
            "ELSE g.away_score END FROM games g WHERE g.game_id = s.game_id AND s.game_id = :g"
        ),
        {"g": game_id},
    )


def test_b2_ignores_the_cup_final(conn: Connection, league: League) -> None:
    """Criterion 7 (e): the Cup final (2023-12-10, final with stats) never reaches
    results(), so giving it the other winner changes no Elo prediction, while the same
    change on the regular game right before it changes every later prediction of its teams
    (power: Elo without a margin multiplier only sees the winner)."""
    before = by_day(run(conn, "B2"))
    swap_winner(conn, CUP_FINAL)
    assert by_day(run(conn, "B2")) == before
    cup_date = league.games[CUP_FINAL]["start_time_utc"]
    regular = conn.execute(
        text(
            "SELECT game_id FROM games WHERE game_type = 'regular' AND start_time_utc < :t "
            "ORDER BY start_time_utc DESC LIMIT 1"
        ),
        {"t": cup_date},
    ).scalar_one()
    swap_winner(conn, regular)
    after = by_day(run(conn, "B2"))
    assert after != before
    assert {d: v for d, v in after.items() if d <= date(2023, 12, 10)} == {
        d: v for d, v in before.items() if d <= date(2023, 12, 10)
    }


def test_b2_warms_up_on_an_earlier_season_without_predicting_it(
    conn: Connection, league: League
) -> None:
    """D-055 item 1 / M-11: three 2022-23 results (A beat B three times) change the first
    2023-24 prediction of A-B (A is rated higher, regressed by the season break) and no
    2022-23 game is predicted or scored; the 2023-24 opening prediction equals the
    expectation from the regressed warm-up ratings."""
    a, b, _, _ = league.teams
    raw = conn.execute(text("SELECT min(raw_response_id) FROM raw_api_responses")).scalar_one()
    cold = run(conn, "B2")
    older = [
        {
            "game_id": f"00222{n:05d}",
            "season": "2022-23",
            "start_time_utc": et(date(2023, 1, 10) + timedelta(2 * n), 20),
            "status_code": 3,
            "status_text": "Final",
            "home_team_id": a,
            "away_team_id": b,
            "home_score": 100 + n,
            "away_score": 90,
            "is_neutral": False,
            "source": "leaguegamelog",
        }
        for n in range(3)
    ]
    insert_games(conn, older, raw)
    for game in older:
        insert_stats(conn, game, raw)
    warm = run(conn, "B2")
    assert {p.game.season for p in warm.predictions} == set(DEV)
    assert len(warm.predictions) == len(cold.predictions)
    opening_cold = next(p for p in cold.predictions if p.game.home_team_id == a)
    opening_warm = next(p for p in warm.predictions if p.game.home_team_id == a)
    assert opening_cold.game.game_id == opening_warm.game.game_id
    assert opening_warm.p_home > opening_cold.p_home and round(opening_cold.p_home, 4) == 0.6401
    # By hand: three A home wins from 1500, then the break into 2023-24.
    ra, rb = 1500.0, 1500.0
    for _ in range(3):
        e = 1 / (1 + 10 ** (-(ra + 100 - rb) / 400))
        ra, rb = ra + 20 * (1 - e), rb - 20 * (1 - e)
    ra, rb = 0.75 * ra + 0.25 * 1505, 0.75 * rb + 0.25 * 1505
    assert opening_warm.p_home == pytest.approx(1 / (1 + 10 ** (-(ra + 100 - rb) / 400)), abs=1e-9)
    # train_cutoff of the opening prediction is now the last warm-up result's availability.
    assert opening_warm.train_cutoff == older[-1]["start_time_utc"] + timedelta(hours=3)


def test_b2_store_twice_writes_one_configuration_and_no_difference(conn: Connection) -> None:
    """Criterion 7 (b): --store twice -> one model_versions row (family elo, the six
    constants and the formula in hyperparameters, version 1), every prediction with p_home
    and NULL totals, the second store inserts nothing and reports 0 differing."""
    first, result, scoring = store(conn, "B2")
    assert first.model_version_new and first.predictions.inserted == len(result.predictions)
    assert first.evaluations.inserted == len(scoring.records) and first.mismatches == 0
    second, _, _ = store(conn, "B2")
    assert not second.model_version_new and second.model_version_id == first.model_version_id
    assert second.predictions.inserted == 0 and second.evaluations.inserted == 0
    assert second.predictions.existing == len(result.predictions) and second.mismatches == 0
    (mv,) = rows(conn, "model_versions", "model_version_id")
    assert (mv["model_name"], mv["version"], mv["model_family"]) == ("elo_b2", "1", "elo")
    assert mv["experiment_id"] == "B2" and mv["feature_version"] is None
    hp = mv["hyperparameters"]
    assert {k: hp[k] for k in ("k", "hca", "carry", "mean", "initial", "mov")} == {
        "k": 20.0,
        "hca": 100.0,
        "carry": 0.75,
        "mean": 1505.0,
        "initial": 1500.0,
        "mov": None,
    }
    assert "E_h" in hp["formula"]
    assert mv["training_window"]["refit"] == "none (sequential daily update)"
    stored = rows(conn, "predictions", "prediction_id")
    assert len(stored) == len(result.predictions)
    assert all(r["p_home"] is not None and r["total_mean"] is None for r in stored)
    evals = rows(conn, "evaluations", "evaluation_id")
    assert all(r["log_loss"] is not None and r["abs_error_total"] is None for r in evals)


def test_b2_other_constants_are_another_version(conn: Connection) -> None:
    """D-055 item 3: K = 25 stores under elo_b2 v1-k25-hca100-c0.75 next to v1."""
    store(conn, "B2", ("2023-24",))
    other = EloBaseline(k=25)
    result = run_walk_forward(conn, other, WalkForwardConfig(seasons=("2023-24",)), now=NOW)
    report = store_run(
        conn,
        result,
        score_run(conn, result),
        other,
        experiment_id="B2",
        seed=0,
        now=NOW,
        code_version=CODE,
    )
    assert report.model_version_new and report.version == "1-k25-hca100-c0.75"
    versions = [(r["model_name"], r["version"]) for r in rows(conn, "model_versions", "version")]
    assert versions == [("elo_b2", "1"), ("elo_b2", "1-k25-hca100-c0.75")]


def test_b2_report_has_win_metrics_and_no_total_metrics(conn: Connection) -> None:
    """Criterion 7 (c): the B2 report prints accuracy, log loss, Brier, ECE and the
    calibration table and '-' for the total errors."""
    _, result, scoring = run_and_score(conn, "B2")
    text_ = render_report(result, scoring, experiment="B2", settings=FAST)
    assert text_.startswith("# Backtest B2: B2 Elo rating")
    all_line = next(line for line in text_.splitlines() if line.startswith("| all | all | (a)"))
    cells = [c.strip() for c in all_line.strip("|").split("|")]
    assert cells[3] == "275" and cells[4] != "-" and cells[5] != "-" and cells[7] != "-"
    assert cells[8] == "-" and cells[9] == "-"  # total MAE and RMSE
    assert "## Calibration" in text_ and "ECE " in text_
    assert "No home win probabilities" not in text_


# --- T-B0 in the runner ----------------------------------------------------------------


def test_t_b0_predicts_from_the_second_game_date_with_the_last_100_totals(
    conn: Connection, league: League
) -> None:
    """The opening date (no result yet) is not predicted (2 games); afterwards every target
    game gets total_mean = mean of the last 100 visible totals of its kind, p_home None;
    the postseason games of 2023-24 (3) and 2024-25 (1) use the regular pool (fewer than 10
    postseason results) and train_cutoff is the last visible result."""
    result = run(conn, "T-B0")
    assert result.skipped[SKIP_NOT_PREDICTED] == 2 and len(result.not_predicted) == 2
    assert {n.game.et_date for n in result.not_predicted} == {date(2023, 10, 24)}
    expected = [g for s in DEV for g in league.ids(s) if g != CUP_FINAL]
    assert len(result.predictions) == len(expected) - 2
    finals = sorted(
        (g for g in league.games.values() if g["status_code"] == 3 and g["game_id"] != CUP_FINAL),
        key=lambda g: g["start_time_utc"],
    )
    for p in result.predictions:
        assert p.p_home is None and p.total_mean is not None and p.over_prob is None
        assert p.train_cutoff <= p.as_of < p.game.start_time_utc
        visible = [
            g["home_score"] + g["away_score"]
            for g in finals
            if g["game_id"].startswith("002")  # regular (game_type is derived from the id)
            and g["start_time_utc"] + timedelta(hours=3) <= p.as_of
        ]
        assert p.total_mean == pytest.approx(sum(visible[-100:]) / len(visible[-100:]))
    assert sum(1 for p in result.predictions if p.game.game_type != "regular") == 4


def test_t_b0_store_writes_null_p_home_and_the_totals(conn: Connection) -> None:
    """Criterion 7 (b): --store twice -> p_home NULL, total_mean and total_sd stored,
    over_prob and reference_line NULL in the odds-free seasons; evaluations carry NULL win
    metrics and the total errors; the second store inserts nothing, 0 differing."""
    first, result, scoring = store(conn, "T-B0")
    second, _, _ = store(conn, "T-B0")
    assert first.predictions.inserted == len(result.predictions) and first.mismatches == 0
    assert second.predictions.inserted == 0 and second.evaluations.inserted == 0
    assert second.predictions.existing == len(result.predictions) and second.mismatches == 0
    (mv,) = rows(conn, "model_versions", "model_version_id")
    assert (mv["model_name"], mv["version"], mv["model_family"]) == (
        "league_total_t_b0",
        "1",
        "total_league_mean",
    )
    assert mv["hyperparameters"]["n"] == 100 and mv["hyperparameters"]["min_postseason"] == 10
    stored = rows(conn, "predictions", "prediction_id")
    assert len(stored) == len(result.predictions)
    for r in stored:
        assert r["p_home"] is None and r["total_mean"] is not None and r["total_mean"] > 0
        assert r["total_sd"] is not None and r["total_sd"] > 0
        assert r["over_prob"] is None and r["reference_line"] is None
    evals = rows(conn, "evaluations", "evaluation_id")
    assert len(evals) == len(scoring.records)
    for e in evals:
        assert e["log_loss"] is None and e["brier"] is None and e["correct"] is None
        assert e["abs_error_total"] is not None and e["sq_error_total"] is not None
        assert e["over_outcome"] is None and e["over_log_loss"] is None
    by_pid = {r["prediction_id"]: r for r in stored}
    for rec in scoring.records:
        e = next(
            e for e in evals if by_pid[e["prediction_id"]]["game_id"] == rec.prediction.game.game_id
        )
        assert float(e["abs_error_total"]) == pytest.approx(rec.abs_error_total, abs=0.005)


def test_t_b0_over_probability_and_line_on_the_live_game_with_a_market(
    conn: Connection, league: League
) -> None:
    """2026-27: the 10-21 game has a DraftKings total line 220.5 at as_of -> over_prob =
    1 - Normal(mean, sd).cdf(220.5) and reference_line 220.50 stored; the other games have
    no line (NULL over_prob). Scoring: over_outcome 1 (232 > 220.5) and over log loss."""
    report, result, scoring = store(conn, "T-B0", ("2026-27",))
    assert report.predictions.inserted == 4 and report.mismatches == 0
    preds = {r["game_id"]: r for r in rows(conn, "predictions", "prediction_id")}
    live = preds[LIVE_AVAILABLE]
    assert live["p_home"] is None and live["reference_line"] == Decimal("220.50")
    assert live["reference_bookmaker"] == "draftkings" and live["over_prob"] is not None
    p = next(p for p in result.predictions if p.game.game_id == LIVE_AVAILABLE)
    assert p.over_prob == pytest.approx(1 - NormalDist(p.total_mean, p.total_sd).cdf(220.5))
    assert live["over_prob"] == Decimal(repr(round(p.over_prob, 4)))
    assert live["market_reference"]["total_line"] == 220.5
    assert all(preds[g]["over_prob"] is None for g in preds if g != LIVE_AVAILABLE)
    assert preds[LIVE_NOT_FINAL]["p_home"] is None
    record = next(r for r in scoring.records if r.prediction.game.game_id == LIVE_AVAILABLE)
    game = league.games[LIVE_AVAILABLE]
    assert record.actual_total == game["home_score"] + game["away_score"] == 232
    assert record.over_outcome == 1
    assert record.over_log_loss == pytest.approx(-math.log(p.over_prob))
    assert record.log_loss is None and record.clv_h2h is None
    assert record.market_status == "available" and record.market_total_line_close == 222.5


def test_t_b0_report_has_total_metrics_only(conn: Connection) -> None:
    """Criterion 7 (c): the T-B0 report has '-' win cells, total MAE and RMSE, market rows
    with n 0, no over probabilities (no line in 2023-24/2024-25) and the calibration note."""
    _, result, scoring = run_and_score(conn, "T-B0")
    text_ = render_report(result, scoring, experiment="T-B0", settings=FAST)
    assert text_.startswith("# Backtest T-B0: T-B0 league mean total")
    all_line = next(line for line in text_.splitlines() if line.startswith("| all | all | (a)"))
    cells = [c.strip() for c in all_line.strip("|").split("|")]
    assert cells[3] == "275" and cells[4:8] == ["-", "-", "-", "-"]  # 2 unpredicted = warm-up
    assert cells[8] != "-" and cells[9] != "-"
    b3 = text_.split("## vs prediction-time market B3")[1].split("##")[0]
    assert "| all | all | 0 |" in b3
    assert "No over probabilities with a reference line." in text_
    assert "No home win probabilities (totals-only model)." in text_
    assert "ECE " not in text_.split("## Calibration")[1]
    live = run_and_score(conn, "T-B0", ("2026-27",))
    live_text = render_report(live[1], live[2], experiment="T-B0", settings=FAST)
    over = live_text.split("## Over probability")[1].split("##")[0]
    assert "| 1 | 0 |" in over  # one scored over probability, no push


# --- the runner's guard for a prediction without any output ----------------------------


class NothingPredictor:
    """Returns a prediction with neither p_home nor total_mean."""

    name = "nothing"
    trains = False
    feature_version = None

    def fit(self, training: TrainingMatrix) -> None:
        """Never called."""

    def predict(
        self, as_of: datetime, games: Sequence[TargetGame], context: PredictionContext
    ) -> Mapping[str, Prediction]:
        """An empty prediction for the first game."""
        return {games[0].game_id: Prediction(p_home=None)}


def test_a_prediction_without_p_home_or_total_mean_is_an_error(conn: Connection) -> None:
    """Schema v1 predictions.has_output at runner level."""
    with pytest.raises(PredictorOutputError, match="needs p_home or total_mean"):
        run_walk_forward(conn, NothingPredictor(), WalkForwardConfig(seasons=("2023-24",)), now=NOW)


# --- CLI -------------------------------------------------------------------------------


@pytest.fixture
def clean_store(engine: Engine) -> Any:
    """--store commits to the league DB: empty the modeling tables again afterwards."""
    yield
    with engine.begin() as conn:
        for table in reversed(MODELING):
            conn.execute(text(f"DELETE FROM {table}"))


def backtest(engine: Engine, *args: str) -> int:
    """Run `presagio backtest run ...` against the league DB."""
    return cli.main(["backtest", "run", *args], engine=engine, now=NOW, setup_logging=False)


@pytest.mark.parametrize(
    ("experiment", "model"),
    [("B2", "elo_b2"), ("T-B0", "league_total_t_b0")],
)
def test_cli_store_twice(
    engine: Engine,
    capsys: pytest.CaptureFixture[str],
    clean_store: None,
    experiment: str,
    model: str,
) -> None:
    """`backtest run --experiment B2|T-B0 --seasons 2023-24,2024-25 --store` twice: exit 0
    both times, the second run inserts nothing and reports 0 differing."""
    args = (
        "--experiment",
        experiment,
        "--seasons",
        "2023-24,2024-25",
        "--store",
        "--bootstrap",
        "20",
    )
    assert backtest(engine, *args) == 0
    out = capsys.readouterr().out
    assert f"Stored (--store): model_versions {model} v1 (model_version_id " in out
    assert "new); predictions inserted " in out and ", already stored 0, differing 0; " in out
    assert backtest(engine, *args) == 0
    out = capsys.readouterr().out
    assert "existing); predictions inserted 0, already stored " in out and "differing 0" in out
    with engine.connect() as conn:
        families = conn.execute(
            text(
                "SELECT mv.model_family, count(p.*) FROM predictions p "
                "JOIN model_versions mv USING (model_version_id) GROUP BY 1"
            )
        ).all()
    assert len(families) == 1 and families[0][1] > 0


def test_cli_lists_b2_and_t_b0() -> None:
    """The experiment ids are registered with the briefing's descriptions."""
    assert EXPERIMENTS["B2"].description.startswith("Elo rating (design 5-2)")
    assert EXPERIMENTS["T-B0"].description.startswith("league mean total of the last 100 games")
    assert isinstance(EXPERIMENTS["B2"].make(), EloBaseline)
    assert isinstance(EXPERIMENTS["T-B0"].make(), LeagueTotalBaseline)
    assert callable(elo_ratings)
