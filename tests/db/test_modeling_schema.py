"""The modeling layer (schema v1 ch. 9) accepts valid rows and names the rule a bad row breaks."""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import URL, Connection, Engine, func, insert, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from presagio.db.models import (
    Evaluation,
    Feature,
    Game,
    ModelVersion,
    Prediction,
    RawApiResponse,
    Team,
)
from presagio.db.models.modeling import MARKET_CLOSE_COLUMNS, MARKET_STATUSES, SNAPSHOT_KINDS
from presagio.db.session import make_engine

GAME = "0022600001"
TIP = datetime(2026, 10, 23, 23, 30, tzinfo=UTC)
# Daily snapshot of that ET date (backtest rule: 07:00 UTC, modeling-v1 1-3).
AS_OF = datetime(2026, 10, 23, 7, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def schema_engine(
    make_test_database: Callable[[], URL], alembic_config: Callable[[Connection], Config]
) -> Iterator[Engine]:
    """Engine on a fresh database migrated to head (dropped again after the test session)."""
    engine = make_engine(make_test_database())
    with engine.begin() as conn:
        command.upgrade(alembic_config(conn), "head")
    yield engine
    engine.dispose()


@pytest.fixture
def conn(schema_engine: Engine) -> Iterator[Connection]:
    """Connection with one game seeded; everything is rolled back after each test."""
    with schema_engine.connect() as connection:
        transaction = connection.begin()
        _seed_game(connection)
        yield connection
        transaction.rollback()


def _seed_game(conn: Connection) -> None:
    """Insert one raw response, two teams and one scheduled game."""
    raw_id = conn.execute(
        insert(RawApiResponse).returning(RawApiResponse.raw_response_id),
        {
            "source": "nba_api",
            "endpoint": "scheduleleaguev2",
            "request_params": {},
            "request_hash": "a" * 64,
            "http_status": 200,
            "response_json": {},
            "response_sha256": "b" * 64,
            "response_bytes": 2,
            "fetched_at": AS_OF,
            "last_fetched_at": AS_OF,
        },
    ).scalar_one()
    conn.execute(
        insert(Team),
        [
            {"team_id": tid, "tricode": code, "city": code, "nickname": code, "full_name": code}
            | {"source": "nba_api.static", "fetched_at": AS_OF}
            for tid, code in ((1610612747, "LAL"), (1610612744, "GSW"))
        ],
    )
    conn.execute(
        insert(Game),
        {
            "game_id": GAME,
            "season": "2026-27",
            "start_time_utc": TIP,
            "is_time_tbd": False,
            "status_code": 1,
            "status_text": "7:30 pm ET",
            "home_team_id": 1610612747,
            "away_team_id": 1610612744,
            "is_neutral": False,
            "if_necessary": False,
            "source": "scheduleleaguev2",
            "fetched_at": AS_OF,
            "raw_response_id": raw_id,
        },
    )


def feature_row(**overrides: Any) -> dict[str, Any]:
    """A valid live daily features row."""
    row = {
        "game_id": GAME,
        "as_of": AS_OF,
        "feature_version": "fv1",
        "snapshot_kind": "daily",
        "origin": "live",
        "values": {"elo_home": 1510.0, "home_rolling_ortg_10": None},  # cold start: JSON null
        "input_fingerprint": {"max_stats_fetched_at": "2026-10-23T06:40:00Z"},
        "computed_at": AS_OF + timedelta(minutes=5),
        "code_version": "abc1234",
    }
    return row | overrides


def model_row(**overrides: Any) -> dict[str, Any]:
    """A valid model configuration (holdout and flags left to their defaults)."""
    row = {
        "model_name": "elo_b2",
        "version": "1",
        "model_family": "elo",
        "feature_version": "fv1",
        "hyperparameters": {"k": 20, "hca": 100, "carry": 0.75},
        "seed": 0,
        "code_version": "abc1234",
        "training_window": {"train_start": "2023-10-24", "refit_every_days": 1},
    }
    return row | overrides


def prediction_row(model_id: int, feature_id: int | None, **overrides: Any) -> dict[str, Any]:
    """A valid live daily prediction with a market reference."""
    row = {
        "game_id": GAME,
        "model_version_id": model_id,
        "as_of": AS_OF,
        "snapshot_kind": "daily",
        "origin": "live",
        "computed_at": AS_OF + timedelta(minutes=6),
        "train_cutoff": AS_OF - timedelta(hours=3),
        "scheduled_start_utc": TIP,
        "feature_id": feature_id,
        "p_home": "0.6123",
        "total_mean": "228.40",
        "over_prob": "0.4800",
        "reference_line": "229.50",
        "reference_bookmaker": "draftkings",
        "market_reference": {"bookmaker": "draftkings", "method": "proportional", "n_books": 5},
        "market_status": "available",
        "is_recommendation_basis": True,
        "code_version": "abc1234",
    }
    return row | overrides


def evaluation_row(prediction_id: int, **overrides: Any) -> dict[str, Any]:
    """A valid evaluation with prediction-time and closing market values."""
    row = {
        "prediction_id": prediction_id,
        "metric_version": "m1",
        "home_won": True,
        "actual_total": 231,
        "actual_margin": 7,
        "actual_start_utc": TIP,
        "rescheduled": False,
        "log_loss": "0.490541",
        "brier": "0.150311",
        "correct": True,
        "abs_error_total": "2.60",
        "sq_error_total": "6.76",
        "over_outcome": 1,
        "over_log_loss": "0.733969",
        "over_brier": "0.270400",
        "market_status": "available",
        "market_p_home_pred": "0.6000",
        "market_p_home_close": "0.6200",
        "market_log_loss_pred": "0.510826",
        "market_log_loss_close": "0.478036",
        "market_brier_pred": "0.160000",
        "market_brier_close": "0.144400",
        "market_total_line_pred": "229.50",
        "market_total_line_close": "230.50",
        "closing_lead_time": timedelta(minutes=40),
        "closing_bookmaker": "draftkings",
        "line_move": "1.00",
        "clv_prob": "0.0200",
        "clv_return": "0.0350",
        "beat_close": True,
        "evaluated_at": TIP + timedelta(hours=12),
        "source_fetched_at": TIP + timedelta(hours=10),
        "code_version": "abc1234",
    }
    return row | overrides


def add(conn: Connection, model: Any, row: dict[str, Any]) -> int:
    """Insert one row and return its surrogate key."""
    key = model.__table__.primary_key.columns.values()[0]
    return conn.execute(insert(model).returning(key), row).scalar_one()


def violation(conn: Connection, model: Any, row: dict[str, Any]) -> str:
    """Insert a row that must fail and return the violated constraint's name."""
    with pytest.raises(IntegrityError) as excinfo, conn.begin_nested():
        conn.execute(insert(model), row)
    return excinfo.value.orig.diag.constraint_name


def chain(conn: Connection) -> tuple[int, int, int]:
    """Insert a feature row, a model and a prediction on them; return their ids."""
    feature_id = add(conn, Feature, feature_row())
    model_id = add(conn, ModelVersion, model_row())
    prediction_id = add(conn, Prediction, prediction_row(model_id, feature_id))
    return feature_id, model_id, prediction_id


def test_value_sets_match_the_design() -> None:
    """The Python value lists are the ones modeling-v1 1-3 and 4-6 name."""
    assert {"daily", "pre_tip_1845", "pre_tip_1945", "pre_tip_2145"} == SNAPSHOT_KINDS
    assert MARKET_STATUSES == (
        "available",
        "pred_only",
        "no_snapshot_at_as_of",
        "no_odds_season",
        "unmatched_event",
        "rescheduled",
    )


def test_valid_rows_and_defaults(conn: Connection) -> None:
    """A full chain is accepted; holdout defaults to 2025-26 and flags to false."""
    _, model_id, prediction_id = chain(conn)
    add(conn, Evaluation, evaluation_row(prediction_id))
    model = conn.execute(
        select(ModelVersion.holdout_seasons, ModelVersion.is_recommendation_model)
    ).one()
    assert model == (["2025-26"], False)
    stored = conn.execute(select(Feature.values)).scalar_one()
    assert stored == {"elo_home": 1510.0, "home_rolling_ortg_10": None}
    # A total-only model needs no p_home and no feature row (modeling-v1 8-2 families).
    total_model = add(
        conn, ModelVersion, model_row(model_name="t_b0", model_family="total_league_mean")
    )
    total_only = prediction_row(
        total_model,
        None,
        p_home=None,
        over_prob=None,
        reference_line=None,
        reference_bookmaker=None,
        market_reference=None,
        market_status="no_odds_season",
        is_recommendation_basis=False,
    )
    add(conn, Prediction, total_only)
    assert conn.execute(select(func.count()).select_from(Prediction)).scalar_one() == 2
    # None in a nullable jsonb column is SQL NULL, not the JSON value null.
    no_market = text("SELECT count(*) FROM predictions WHERE market_reference IS NULL")
    assert conn.execute(no_market).scalar_one() == 1


def test_feature_key_and_no_overwrite(conn: Connection) -> None:
    """Same (game, as_of, version) is refused; DO NOTHING keeps the stored values (T11)."""
    add(conn, Feature, feature_row())
    assert violation(conn, Feature, feature_row()) == "uq_features_game_id_as_of_feature_version"
    recomputed = feature_row(values={"elo_home": 9999.0})
    inserted = conn.execute(
        pg_insert(Feature).values(recomputed).on_conflict_do_nothing().returning(Feature.feature_id)
    ).all()
    assert inserted == []
    assert conn.execute(select(Feature.values)).scalar_one()["elo_home"] == 1510.0
    # A new feature version is a new row.
    add(conn, Feature, feature_row(feature_version="fv2"))


def test_live_cap_applies_to_live_rows_only(conn: Connection) -> None:
    """A live row computed over 2h after as_of is refused; backtest and gap-fill rows are not."""
    late = AS_OF + timedelta(hours=2, seconds=1)
    assert (
        violation(conn, Feature, feature_row(computed_at=late))
        == "ck_features_live_computed_within_2h"
    )
    add(conn, Feature, feature_row(computed_at=AS_OF + timedelta(hours=2)))
    a_year_later = AS_OF + timedelta(days=365)
    add(
        conn,
        Feature,
        feature_row(origin="backtest", feature_version="fv2", computed_at=a_year_later),
    )


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"computed_at": AS_OF - timedelta(seconds=1)}, "ck_features_as_of_before_computed"),
        ({"origin": "replay"}, "ck_features_origin_valid"),
        ({"snapshot_kind": "Daily"}, "ck_features_snapshot_kind_format"),
        ({"snapshot_kind": "pre_tip_845"}, "ck_features_snapshot_kind_format"),
        ({"values": [1, 2]}, "ck_features_values_is_object"),
        ({"input_fingerprint": "x"}, "ck_features_input_fingerprint_is_object"),
        ({"game_id": "0022699999"}, "fk_features_game_id_games"),
    ],
)
def test_bad_feature_rows_are_refused(
    conn: Connection, overrides: dict[str, Any], constraint: str
) -> None:
    """Each broken features row is refused by the named rule."""
    assert violation(conn, Feature, feature_row(**overrides)) == constraint


def test_snapshot_kinds_from_the_design_pass_the_format(conn: Connection) -> None:
    """Every listed snapshot kind passes the database format check."""
    for i, kind in enumerate(sorted(SNAPSHOT_KINDS)):
        add(conn, Feature, feature_row(snapshot_kind=kind, feature_version=f"fv{i}"))


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"is_recommendation_model": True}, "ck_model_versions_recommendation_has_artifact"),
        ({"holdout_unlocked_reason": "  "}, "ck_model_versions_unlock_reason_not_blank"),
        ({"model_family": "LightGBM"}, "ck_model_versions_model_family_format"),
        ({"hyperparameters": []}, "ck_model_versions_hyperparameters_is_object"),
        # Python None is SQL NULL (not JSON null), so NOT NULL refuses it; it has no name.
        ({"training_window": None}, None),
    ],
)
def test_bad_model_versions_are_refused(
    conn: Connection, overrides: dict[str, Any], constraint: str | None
) -> None:
    """Each broken model_versions row is refused by the named rule."""
    assert violation(conn, ModelVersion, model_row(**overrides)) == constraint


def test_model_key_and_recommendation_model_with_artifact(conn: Connection) -> None:
    """(model_name, version) is unique; a recommendation model with an artifact is fine."""
    add(conn, ModelVersion, model_row())
    assert violation(conn, ModelVersion, model_row()) == "uq_model_versions_model_name_version"
    add(
        conn,
        ModelVersion,
        model_row(version="2", is_recommendation_model=True, artifact_path="a/b"),
    )


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"p_home": "1.0000"}, "ck_predictions_p_home_range"),
        ({"p_home": "0"}, "ck_predictions_p_home_range"),
        ({"p_home": None, "total_mean": None, "over_prob": None}, "ck_predictions_has_output"),
        (
            {"train_cutoff": AS_OF + timedelta(seconds=1)},
            "ck_predictions_train_cutoff_before_as_of",
        ),
        ({"scheduled_start_utc": AS_OF}, "ck_predictions_as_of_before_start"),
        ({"computed_at": AS_OF - timedelta(seconds=1)}, "ck_predictions_as_of_before_computed"),
        ({"reference_line": None}, "ck_predictions_over_needs_line"),
        ({"reference_bookmaker": None}, "ck_predictions_line_has_bookmaker"),
        (
            {"total_mean": None, "total_sd": "20.00", "over_prob": None},
            "ck_predictions_sd_needs_mean",
        ),
        ({"market_status": "closed"}, "ck_predictions_market_status_valid"),
        ({"origin": "replay"}, "ck_predictions_origin_valid"),
        ({"market_reference": [1]}, "ck_predictions_market_reference_is_object"),
    ],
)
def test_bad_predictions_are_refused(
    conn: Connection, overrides: dict[str, Any], constraint: str
) -> None:
    """Each broken predictions row is refused by the named rule."""
    model_id = add(conn, ModelVersion, model_row())
    assert violation(conn, Prediction, prediction_row(model_id, None, **overrides)) == constraint


def test_one_recommendation_basis_per_game_and_model(conn: Connection) -> None:
    """Only one basis snapshot per game and model; other snapshots of it are fine (D-033)."""
    _, model_id, _ = chain(conn)
    later = AS_OF + timedelta(hours=16)  # a pre-tip snapshot of the same game
    second_basis = prediction_row(
        model_id, None, as_of=later, computed_at=later, snapshot_kind="pre_tip_1845"
    )
    assert violation(conn, Prediction, second_basis) == "uq_predictions_one_recommendation_basis"
    add(conn, Prediction, second_basis | {"is_recommendation_basis": False})
    same_key = prediction_row(model_id, None, is_recommendation_basis=False)
    assert violation(conn, Prediction, same_key) == "uq_predictions_game_id_model_version_id_as_of"


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"home_won": False}, "ck_evaluations_home_won_matches_margin"),
        ({"actual_margin": 0, "home_won": False}, "ck_evaluations_margin_nonzero"),
        ({"actual_margin": 240}, "ck_evaluations_margin_within_total"),
        ({"brier": None}, "ck_evaluations_win_metrics_together"),
        ({"sq_error_total": None}, "ck_evaluations_total_errors_together"),
        ({"over_outcome": None}, "ck_evaluations_over_metrics_together"),
        ({"over_outcome": 2}, "ck_evaluations_over_outcome_valid"),
        ({"brier": "1.000001"}, "ck_evaluations_brier_range"),
        (
            {"market_status": "nope"} | dict.fromkeys(MARKET_CLOSE_COLUMNS),
            "ck_evaluations_market_status_valid",
        ),
        ({"closing_lead_time": timedelta(0)}, "ck_evaluations_closing_lead_time_positive"),
        # No market at all: every market column must be empty (D-026). Close columns are
        # cleared so that only this rule is broken (PostgreSQL checks CHECKs in name order).
        (
            {"market_status": "no_odds_season"} | dict.fromkeys(MARKET_CLOSE_COLUMNS),
            "ck_evaluations_no_market_all_null",
        ),
        # pred_only: closing outside the tolerance, so no close price or CLV (modeling-v1 4-6).
        ({"market_status": "pred_only"}, "ck_evaluations_close_only_when_available"),
        ({"rescheduled": True}, "ck_evaluations_rescheduled_no_market"),
    ],
)
def test_bad_evaluations_are_refused(
    conn: Connection, overrides: dict[str, Any], constraint: str
) -> None:
    """Each broken evaluations row is refused by the named rule."""
    _, _, prediction_id = chain(conn)
    assert violation(conn, Evaluation, evaluation_row(prediction_id, **overrides)) == constraint


def test_market_statuses_without_comparison(conn: Connection) -> None:
    """No-market rows store NULL market columns; pred_only keeps the lead time as the reason."""
    _, _, prediction_id = chain(conn)
    no_market = dict.fromkeys(
        [
            "market_p_home_pred",
            "market_p_home_close",
            "market_log_loss_pred",
            "market_log_loss_close",
            "market_brier_pred",
            "market_brier_close",
            "market_total_line_pred",
            "market_total_line_close",
            "closing_lead_time",
            "closing_bookmaker",
            "line_move",
            "clv_prob",
            "clv_return",
            "beat_close",
        ]
    )
    pred_side = {
        "market_p_home_pred": "0.6000",
        "market_log_loss_pred": "0.510826",
        "market_brier_pred": "0.160000",
        "market_total_line_pred": "229.50",
    }
    rows = [
        evaluation_row(prediction_id, metric_version="m1", market_status="no_odds_season")
        | no_market,
        evaluation_row(prediction_id, metric_version="m2", market_status="pred_only")
        | no_market
        | pred_side
        | {"closing_lead_time": timedelta(hours=9), "closing_bookmaker": "draftkings"},
        evaluation_row(prediction_id, metric_version="m3", market_status="rescheduled")
        | no_market
        | {"rescheduled": True},
    ]
    for row in rows:
        add(conn, Evaluation, row)
    # A rescheduled game must say so in market_status's partner flag too.
    wrong = evaluation_row(prediction_id, metric_version="m4", market_status="rescheduled")
    assert violation(conn, Evaluation, wrong | no_market) == "ck_evaluations_rescheduled_status"
    again = evaluation_row(prediction_id, metric_version="m1", market_status="no_odds_season")
    assert (
        violation(conn, Evaluation, again | no_market)
        == "uq_evaluations_prediction_id_metric_version"
    )


def test_recommendation_basis_view(conn: Connection) -> None:
    """The view lists basis predictions only, with their evaluation when there is one."""
    _, model_id, prediction_id = chain(conn)
    later = AS_OF + timedelta(hours=16)
    add(
        conn,
        Prediction,
        prediction_row(
            model_id,
            None,
            as_of=later,
            computed_at=later,
            snapshot_kind="pre_tip_1845",
            is_recommendation_basis=False,
        ),
    )
    view = "SELECT prediction_id, metric_version, evaluation_market_status FROM {}"
    query = text(view.format("predictions_recommendation_basis"))
    assert conn.execute(query).all() == [(prediction_id, None, None)]
    add(conn, Evaluation, evaluation_row(prediction_id))
    assert conn.execute(query).all() == [(prediction_id, "m1", "available")]
