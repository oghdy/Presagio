"""The store path (P-033 criterion 3, 4, 5 (c)): model_versions, predictions, evaluations on
the temp league DB, append only, differences reported and never overwritten, holdout rows
never written without an unlock."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Connection, text

from presagio.evaluation.dummy import ConstantPredictor
from presagio.evaluation.experiments import EXPERIMENTS
from presagio.evaluation.holdout import HoldoutDecision
from presagio.evaluation.scoring import ScoringResult, score_run
from presagio.evaluation.store import (
    StoreError,
    StoreReport,
    prediction_row,
    probability,
    quantize,
    store_run,
)
from presagio.evaluation.walkforward import WalkForwardConfig, WalkForwardResult, run_walk_forward
from presagio.models.baselines import AlwaysHomeBaseline

from .league import LIVE_AVAILABLE, LIVE_NOT_FINAL, LIVE_PRED_ONLY, LIVE_UNMATCHED, League

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
CODE = "test1234"
TABLES = ("model_versions", "predictions", "evaluations")
UNLOCK = "H-08 final model"


def run_and_score(
    conn: Connection, experiment: str = "B0", seasons: tuple[str, ...] = ("2023-24",), **config: Any
) -> tuple[Any, WalkForwardResult, ScoringResult]:
    """A registered experiment over the seasons, scored."""
    predictor = EXPERIMENTS[experiment].make()
    run = run_walk_forward(conn, predictor, WalkForwardConfig(seasons=seasons, **config), now=NOW)
    return predictor, run, score_run(conn, run)


def store(
    conn: Connection, predictor: Any, run: WalkForwardResult, scoring: ScoringResult, **kw: Any
) -> StoreReport:
    """store_run with the test defaults."""
    kw.setdefault("experiment_id", "B0")
    return store_run(conn, run, scoring, predictor, seed=0, now=NOW, code_version=CODE, **kw)


def counts(conn: Connection) -> dict[str, int]:
    """Row counts of the three modeling tables."""
    return {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one() for t in TABLES}


def rows(conn: Connection, table: str, order: str) -> list[dict[str, Any]]:
    """Every row of a table."""
    return [
        dict(r) for r in conn.execute(text(f"SELECT * FROM {table} ORDER BY {order}")).mappings()
    ]


def test_store_writes_the_configuration_a_prediction_and_an_evaluation_per_game(
    conn: Connection,
) -> None:
    """B0 over 2023-24: one model_versions row with the reproduction columns, 174
    predictions (origin backtest, daily, feature_id NULL, total_mean NULL, no_odds_season,
    no market reference, not a recommendation basis) and 174 evaluations (metric_version
    mv1, market columns NULL), every value equal to the run's after quantization."""
    predictor, run, scoring = run_and_score(conn)
    report = store(conn, predictor, run, scoring)
    assert counts(conn) == {"model_versions": 1, "predictions": 174, "evaluations": 174}
    assert report.model_version_new and report.predictions.inserted == 174
    assert report.evaluations.inserted == 174 and report.mismatches == 0
    assert report.holdout_skipped_predictions == 0 and report.holdout_reason_recorded is None
    (mv,) = rows(conn, "model_versions", "model_version_id")
    assert (mv["model_name"], mv["version"], mv["model_family"]) == (
        "always_home_b0",
        "1",
        "baseline_home",
    )
    assert mv["feature_version"] is None and mv["tuning_data"] is None
    assert mv["hyperparameters"]["min_season_games"] == 100
    assert mv["seed"] == 0 and mv["code_version"] == CODE and mv["experiment_id"] == "B0"
    assert mv["training_window"]["availability_lag_hours"] == 3.0
    assert mv["training_window"]["market"]["margin_method"] == "proportional"
    assert mv["holdout_seasons"] == ["2025-26"] and mv["holdout_unlocked_reason"] is None
    assert mv["artifact_path"] is None and mv["is_recommendation_model"] is False
    stored = {r["game_id"]: r for r in rows(conn, "predictions", "prediction_id")}
    for p in run.predictions:
        r = stored[p.game.game_id]
        assert r["model_version_id"] == mv["model_version_id"]
        assert (r["origin"], r["snapshot_kind"]) == ("backtest", "daily")
        assert r["as_of"] == p.as_of and r["computed_at"] == NOW
        assert r["train_cutoff"] == p.train_cutoff <= r["as_of"] < r["scheduled_start_utc"]
        assert r["scheduled_start_utc"] == p.game.start_time_utc
        assert r["feature_id"] is None and r["total_mean"] is None and r["over_prob"] is None
        assert r["p_home"] == probability(p.p_home) and abs(float(r["p_home"]) - p.p_home) < 1e-4
        assert r["reference_line"] is None and r["reference_bookmaker"] is None
        assert r["market_reference"] is None and r["market_status"] == "no_odds_season"
        assert r["is_recommendation_basis"] is False and r["code_version"] == CODE
    by_pid = {r["prediction_id"]: r for r in rows(conn, "evaluations", "evaluation_id")}
    assert len(by_pid) == 174
    for rec in scoring.records:
        e = by_pid[stored[rec.prediction.game.game_id]["prediction_id"]]
        assert e["metric_version"] == "mv1" and e["evaluated_at"] == NOW
        assert e["home_won"] == rec.home_won and e["actual_total"] == rec.actual_total
        assert e["actual_margin"] == rec.actual_margin and e["rescheduled"] is False
        assert e["log_loss"] == quantize(rec.log_loss, 6) and e["brier"] == quantize(rec.brier, 6)
        assert e["correct"] == rec.correct and e["abs_error_total"] is None
        assert e["over_outcome"] is None and e["market_status"] == "no_odds_season"
        assert e["market_p_home_pred"] is None and e["clv_return"] is None
        assert e["source_fetched_at"] == rec.source_fetched_at and e["code_version"] == CODE


def test_storing_twice_changes_nothing_and_reports_no_difference(conn: Connection) -> None:
    """Criterion 3: the same run stored twice -> same row counts, every row 'already stored',
    0 differences, the configuration row reused."""
    predictor, run, scoring = run_and_score(conn, seasons=("2023-24", "2024-25"))
    first = store(conn, predictor, run, scoring)
    before = counts(conn)
    second = store(conn, predictor, run, scoring)
    assert counts(conn) == before == {"model_versions": 1, "predictions": 345, "evaluations": 345}
    assert first.predictions.inserted == 345 and second.predictions.inserted == 0
    assert second.predictions.existing == 345 and second.evaluations.existing == 345
    assert second.mismatches == 0 and not second.model_version_new
    assert second.model_version_id == first.model_version_id


def test_changed_values_are_reported_with_examples_and_never_overwritten(
    conn: Connection,
) -> None:
    """Criterion 3 and D-055 item 2: a prediction whose p_home differs is counted and shown
    and gets NO evaluation row from this run (its stored evaluation stays); an evaluation
    whose log loss differs (prediction unchanged) is counted and shown; the stored rows keep
    their first values; row counts stay."""
    predictor, run, scoring = run_and_score(conn)
    store(conn, predictor, run, scoring)
    before = counts(conn)
    changed_prediction = replace(run.predictions[10], p_home=0.1234)
    changed_run = replace(
        run, predictions=[*run.predictions[:10], changed_prediction, *run.predictions[11:]]
    )
    other = run.predictions[20].game.game_id
    record = next(r for r in scoring.records if r.prediction.game.game_id == other)
    changed_record = replace(record, log_loss=9.0, brier=0.999)
    changed_scoring = ScoringResult(
        [changed_record, *(r for r in scoring.records if r is not record)], scoring.not_final
    )
    report = store(conn, predictor, changed_run, changed_scoring)
    assert counts(conn) == before
    assert (report.predictions.mismatched, report.predictions.existing) == (1, 173)
    assert report.evaluations_skipped_for_mismatch == 1
    assert (report.evaluations.mismatched, report.evaluations.existing) == (1, 172)
    assert report.evaluations.total == 173 and report.mismatches == 2
    assert len(report.predictions.examples) == 1 and "p_home" in report.predictions.examples[0]
    assert changed_prediction.game.game_id in report.predictions.examples[0]
    assert "log_loss" in report.evaluations.examples[0]
    assert "brier" in report.evaluations.examples[0]
    stored = conn.execute(
        text("SELECT p_home FROM predictions WHERE game_id = :g"),
        {"g": changed_prediction.game.game_id},
    ).scalar_one()
    assert stored == probability(run.predictions[10].p_home) != Decimal("0.1234")
    stored_ll = conn.execute(
        text(
            "SELECT e.log_loss FROM evaluations e JOIN predictions p USING (prediction_id) "
            "WHERE p.game_id = :g"
        ),
        {"g": other},
    ).scalar_one()
    assert stored_ll == quantize(record.log_loss, 6) != Decimal("9.000000")


def test_locked_holdout_rows_are_never_written(conn: Connection) -> None:
    """Criterion 4: a run that (somehow) holds 2025-26 predictions under a locked holdout
    stores none of them, counts them, and leaves holdout_unlocked_reason NULL."""
    predictor, run, scoring = run_and_score(conn, seasons=("2025-26",), unlock_holdout=UNLOCK)
    assert len(run.predictions) == 20
    locked = replace(run, holdout=HoldoutDecision(("2025-26",), (), None))
    report = store(conn, predictor, locked, scoring)
    assert counts(conn) == {"model_versions": 1, "predictions": 0, "evaluations": 0}
    assert report.holdout_skipped_predictions == 20 and report.holdout_skipped_evaluations == 20
    assert report.predictions.total == 0 and report.evaluations.total == 0
    (mv,) = rows(conn, "model_versions", "model_version_id")
    assert mv["holdout_unlocked_reason"] is None


def test_unlocked_run_writes_holdout_rows_and_records_the_reason(conn: Connection) -> None:
    """Criterion 4: with --unlock-holdout the 2025-26 rows are written and the reason lands
    in model_versions.holdout_unlocked_reason, also on a configuration row that a locked
    run created earlier; a second unlock with another reason is refused."""
    predictor, dev_run, dev_scoring = run_and_score(conn, seasons=("2024-25",))
    first = store(conn, predictor, dev_run, dev_scoring)
    assert first.holdout_reason_recorded is None
    _, run, scoring = run_and_score(conn, seasons=("2025-26",), unlock_holdout=UNLOCK)
    report = store(conn, predictor, run, scoring)
    assert report.holdout_reason_recorded == UNLOCK and not report.model_version_new
    assert report.predictions.inserted == 20 and report.holdout_skipped_predictions == 0
    (mv,) = rows(conn, "model_versions", "model_version_id")
    assert mv["holdout_unlocked_reason"] == UNLOCK
    seasons = conn.execute(
        text("SELECT DISTINCT g.season FROM predictions p JOIN games g USING (game_id)")
    ).scalars()
    assert set(seasons) == {"2024-25", "2025-26"}
    again = store(conn, predictor, run, scoring)
    assert again.holdout_reason_recorded is None and again.mismatches == 0
    other = replace(run, holdout=HoldoutDecision(("2025-26",), ("2025-26",), "H-11 strategy"))
    with pytest.raises(StoreError, match="already unlocked"):
        store(conn, predictor, other, scoring)


def test_a_different_configuration_under_the_same_version_is_refused(conn: Connection) -> None:
    """model_versions is append only too: B0 with min_season_games 50 is another
    configuration under the same (model_name, version) -> StoreError, nothing written."""
    predictor, run, scoring = run_and_score(conn)
    store(conn, predictor, run, scoring)
    before = counts(conn)
    other = AlwaysHomeBaseline(min_season_games=50)
    other_run = run_walk_forward(conn, other, WalkForwardConfig(seasons=("2023-24",)), now=NOW)
    with pytest.raises(StoreError, match="different configuration"):
        store(conn, other, other_run, score_run(conn, other_run))
    assert counts(conn) == before
    lagged = run_walk_forward(
        conn,
        AlwaysHomeBaseline(),
        WalkForwardConfig(seasons=("2023-24",), availability_lag=timedelta(hours=2)),
        now=NOW,
    )
    with pytest.raises(StoreError, match="training_window"):
        store(conn, AlwaysHomeBaseline(), lagged, score_run(conn, lagged))


def test_the_framework_dummy_cannot_be_stored(conn: Connection) -> None:
    """H-00 has no ModelSpec."""
    dummy = ConstantPredictor()
    run = run_walk_forward(conn, dummy, WalkForwardConfig(seasons=("2023-24",)), now=NOW)
    with pytest.raises(StoreError, match="no ModelSpec"):
        store(conn, dummy, run, score_run(conn, run), experiment_id="H-00")
    assert counts(conn) == {"model_versions": 0, "predictions": 0, "evaluations": 0}


def test_live_season_rows_carry_the_market_columns(conn: Connection, league: League) -> None:
    """B1 over 2026-27 (0.5 everywhere: no season record yet). Predictions: available /
    unmatched_event / no_snapshot_at_as_of at as_of with a copied market reference where a
    market exists. Evaluations: the available game's close, CLV (+0.0769, beat_close) and
    line move +2.00 by hand (tests/evaluation/test_scoring.py), pred_only without close,
    unmatched with every market column NULL; the unfinished game has no evaluation."""
    predictor, run, scoring = run_and_score(conn, "B1", seasons=("2026-27",))
    report = store(conn, predictor, run, scoring, experiment_id="B1")
    assert report.predictions.inserted == 4 and report.evaluations.inserted == 3
    preds = {r["game_id"]: r for r in rows(conn, "predictions", "prediction_id")}
    assert {g: r["market_status"] for g, r in preds.items()} == {
        LIVE_UNMATCHED: "unmatched_event",
        LIVE_AVAILABLE: "available",
        LIVE_PRED_ONLY: "available",
        LIVE_NOT_FINAL: "no_snapshot_at_as_of",
    }
    available = preds[LIVE_AVAILABLE]
    assert available["p_home"] == Decimal("0.5000")
    assert available["reference_line"] == Decimal("220.50")
    assert available["reference_bookmaker"] == "draftkings"
    ref = available["market_reference"]
    assert ref["method"] == "proportional" and ref["n_books"] == 3
    assert ref["bookmakers"] == ["betmgm", "draftkings", "fanduel"]
    assert ref["total_line"] == 220.5 and len(ref["snapshot_ids"]) == 6
    assert preds[LIVE_UNMATCHED]["market_reference"] is None
    assert preds[LIVE_NOT_FINAL]["market_reference"] is None
    assert preds[LIVE_NOT_FINAL]["train_cutoff"] <= preds[LIVE_NOT_FINAL]["as_of"]
    evals = {r["prediction_id"]: r for r in rows(conn, "evaluations", "evaluation_id")}
    e = evals[available["prediction_id"]]
    assert e["market_status"] == "available" and e["home_won"] is False
    assert e["market_p_home_pred"] == Decimal("0.4615")
    assert e["market_p_home_close"] == probability((1 / 1.92) / (1 / 1.92 + 1 / 1.98))
    assert e["market_total_line_pred"] == Decimal("220.50")
    assert e["market_total_line_close"] == Decimal("222.50") and e["line_move"] == Decimal("2.00")
    assert e["closing_lead_time"] == timedelta(minutes=45) and e["closing_bookmaker"] == "consensus"
    assert e["clv_return"] == Decimal("0.0769") and e["beat_close"] is True
    assert e["clv_prob"] == quantize(0.5128205128 - 0.4615384615, 4)  # close - pred, home at dk
    assert e["over_outcome"] == 1 and e["over_log_loss"] is None
    pred_only = evals[preds[LIVE_PRED_ONLY]["prediction_id"]]
    assert pred_only["market_status"] == "pred_only"
    assert pred_only["market_p_home_pred"] == Decimal("0.5000")
    assert pred_only["market_p_home_close"] is None and pred_only["clv_return"] is None
    unmatched = evals[preds[LIVE_UNMATCHED]["prediction_id"]]
    assert unmatched["market_status"] == "unmatched_event"
    assert unmatched["market_p_home_pred"] is None and unmatched["closing_lead_time"] is None
    assert preds[LIVE_NOT_FINAL]["prediction_id"] not in evals


def test_market_reference_is_a_detached_json_copy(conn: Connection) -> None:
    """Criterion 5 (c): the stored market_reference is a JSON copy of the market summary
    (lists, no shared objects); changing the copy changes nothing in the GameMarket."""
    _, run, _ = run_and_score(conn, "B1", seasons=("2026-27",))
    p = next(p for p in run.predictions if p.game.game_id == LIVE_AVAILABLE)
    row = prediction_row(p, 1, NOW, CODE)
    reference = row["market_reference"]
    assert reference is not None and reference == p.market.reference()
    assert isinstance(reference["snapshot_ids"], list)
    reference["snapshot_ids"].append(999)
    reference["bookmakers"].clear()
    assert p.market.reference()["snapshot_ids"] == list(p.market.home.snapshot_ids)
    assert p.market.reference()["bookmakers"] == ["betmgm", "draftkings", "fanduel"]
    assert row["train_cutoff"] == p.train_cutoff and row["feature_id"] is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.99996, Decimal("0.9999")),
        (0.00001, Decimal("0.0001")),
        (0.46153846, Decimal("0.4615")),
        (0.5, Decimal("0.5000")),
        (None, None),
    ],
)
def test_probabilities_are_clipped_into_the_open_interval(
    value: float | None, expected: Decimal | None
) -> None:
    """Schema v1 9-1 item 8: numeric(6,4) must stay inside (0, 1)."""
    assert probability(value) == expected


def test_quantize_rounds_half_up_like_postgresql() -> None:
    """0.1234565 -> 0.123457 at 6 decimals; Decimals pass through; None stays None."""
    assert quantize(0.1234565, 6) == Decimal("0.123457")
    assert quantize(Decimal("2.5"), 0) == Decimal("3")
    assert quantize(220.5, 2) == Decimal("220.50")
    assert quantize(None, 2) is None
