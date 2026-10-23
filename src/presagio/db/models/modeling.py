"""Modeling layer tables: features, model_versions, predictions, evaluations (schema v1 ch. 9).

These four tables are the ML <-> Serving contract (D-004, D-049, docs/design/modeling-v1.md
ch. 8). features and predictions are append-only: loaders insert with ON CONFLICT DO NOTHING
and report a mismatch instead of updating (schema v1 9-1). That is a loading rule, not a
constraint, so it lives in the loaders (P-033, P-035) and the feature_drift quality check.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, INTERVAL, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from presagio.db.base import Base, nonnegative

# Values loaders must use. snapshot_kind grows with new capture times (P-028 adds weekend
# ones), so the database only checks its shape and loaders reject unknown kinds (as D-037
# does for job_type). origin and market_status are fixed sets, checked by the database.
SNAPSHOT_KINDS = frozenset({"daily", "pre_tip_1845", "pre_tip_1945", "pre_tip_2145"})
SNAPSHOT_KIND_PATTERN = "^(daily|pre_tip_[0-9]{4})$"
# Gap-filling rows computed later for a missed live day are 'backtest' (D-049 follow-up).
ORIGINS = ("live", "backtest")
# modeling-v1 4-6 / D-049 M-8.
MARKET_STATUSES = (
    "available",
    "pred_only",
    "no_snapshot_at_as_of",
    "no_odds_season",
    "unmatched_event",
    "rescheduled",
)
# modeling-v1 8-2; new families arrive with new hypotheses, so loaders check membership.
MODEL_FAMILIES = frozenset(
    {
        "baseline_home",
        "baseline_season_wr",
        "elo",
        "market",
        "logistic",
        "lightgbm",
        "simulation",
        "stacking",
        "total_league_mean",
        "total_pace_eff",
    }
)
MODEL_FAMILY_PATTERN = "^[a-z][a-z0-9_]*$"
DEFAULT_HOLDOUT_SEASONS = "'{2025-26}'::text[]"
# A live row computed more than this long after its as_of is not stored (modeling-v1 1-3, R4).
LIVE_MAX_COMPUTE_LAG = "interval '2 hours'"

ORIGIN_IN = "origin IN ('live', 'backtest')"
MARKET_STATUS_IN = "market_status IN ({})".format(", ".join(f"'{s}'" for s in MARKET_STATUSES))
# Evaluation columns that exist only when the market can be compared (modeling-v1 4-6, 8-4).
MARKET_PRED_COLUMNS = (
    "market_p_home_pred",
    "market_log_loss_pred",
    "market_brier_pred",
    "market_total_line_pred",
)
MARKET_CLOSE_COLUMNS = (
    "market_p_home_close",
    "market_log_loss_close",
    "market_brier_close",
    "market_total_line_close",
    "line_move",
    "clv_prob",
    "clv_return",
    "beat_close",
)
CLOSING_INFO_COLUMNS = ("closing_lead_time", "closing_bookmaker")

# Python None must mean SQL NULL here; SQLAlchemy's default JSONB stores it as JSON 'null',
# which a nullable column would keep and the *_is_object CHECKs would refuse.
JSONB_NONE_IS_NULL = JSONB(none_as_null=True)


def is_object(column: str) -> CheckConstraint:
    """Return the table-level CHECK that a jsonb column holds an object (NULL passes)."""
    return CheckConstraint(f"jsonb_typeof({column}) = 'object'", name=f"{column}_is_object")


def open_unit_interval(column: str) -> CheckConstraint:
    """Return the table-level CHECK 0 < column < 1, named ck_<table>_<column>_range."""
    return CheckConstraint(f"{column} > 0 AND {column} < 1", name=f"{column}_range")


def closed_unit_interval(column: str) -> CheckConstraint:
    """Return the table-level CHECK 0 <= column <= 1, named ck_<table>_<column>_range."""
    return CheckConstraint(f"{column} BETWEEN 0 AND 1", name=f"{column}_range")


class Feature(Base):
    """Feature values for one game at one as_of under one feature version; never updated."""

    __tablename__ = "features"

    feature_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.game_id"))
    as_of: Mapped[datetime]
    feature_version: Mapped[str]
    snapshot_kind: Mapped[str]
    origin: Mapped[str]
    values: Mapped[dict[str, Any]] = mapped_column(JSONB_NONE_IS_NULL)
    input_fingerprint: Mapped[dict[str, Any]] = mapped_column(JSONB_NONE_IS_NULL)
    # Same Python clock as as_of (rule R4): a DB now() default would race it (D-030 lesson).
    computed_at: Mapped[datetime]
    code_version: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        UniqueConstraint("game_id", "as_of", "feature_version"),
        CheckConstraint(f"snapshot_kind ~ '{SNAPSHOT_KIND_PATTERN}'", name="snapshot_kind_format"),
        CheckConstraint(ORIGIN_IN, name="origin_valid"),
        is_object("values"),
        is_object("input_fingerprint"),
        CheckConstraint("as_of <= computed_at", name="as_of_before_computed"),
        # Backtest and gap-filling rows have a past as_of, so only live rows get the cap.
        CheckConstraint(
            f"origin <> 'live' OR computed_at - as_of <= {LIVE_MAX_COMPUTE_LAG}",
            name="live_computed_within_2h",
        ),
        Index("ix_features_as_of", "as_of"),
        Index("ix_features_feature_version", "feature_version"),
    )


class ModelVersion(Base):
    """One model configuration; weekly refits keep the row and differ by train_cutoff."""

    __tablename__ = "model_versions"

    model_version_id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    model_name: Mapped[str]
    version: Mapped[str]
    model_family: Mapped[str]
    # NULL for models that read no features row (e.g. the home-team baseline).
    feature_version: Mapped[str | None]
    hyperparameters: Mapped[dict[str, Any]] = mapped_column(JSONB_NONE_IS_NULL)
    seed: Mapped[int]
    code_version: Mapped[str]
    training_window: Mapped[dict[str, Any]] = mapped_column(JSONB_NONE_IS_NULL)
    tuning_data: Mapped[str | None]
    experiment_id: Mapped[str | None]
    holdout_seasons: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text(DEFAULT_HOLDOUT_SEASONS)
    )
    # NULL means the holdout seasons were never opened for this configuration.
    holdout_unlocked_reason: Mapped[str | None]
    artifact_path: Mapped[str | None]
    is_recommendation_model: Mapped[bool] = mapped_column(server_default=text("false"))
    retired_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        UniqueConstraint("model_name", "version"),
        CheckConstraint(f"model_family ~ '{MODEL_FAMILY_PATTERN}'", name="model_family_format"),
        is_object("hyperparameters"),
        is_object("training_window"),
        CheckConstraint("btrim(holdout_unlocked_reason) <> ''", name="unlock_reason_not_blank"),
        # The recommendation model's fitted artifacts are mandatory (modeling-v1 8-2).
        CheckConstraint(
            "NOT is_recommendation_model OR artifact_path IS NOT NULL",
            name="recommendation_has_artifact",
        ),
    )


class Prediction(Base):
    """One model version's prediction for one game at one as_of; never updated."""

    __tablename__ = "predictions"

    prediction_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.game_id"))
    model_version_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("model_versions.model_version_id")
    )
    as_of: Mapped[datetime]
    snapshot_kind: Mapped[str]
    origin: Mapped[str]
    computed_at: Mapped[datetime]
    train_cutoff: Mapped[datetime]
    # games.start_time_utc when the prediction was made; evaluation compares it (rescheduled).
    scheduled_start_utc: Mapped[datetime]
    # NULL only for models that read no features row (model_versions.feature_version NULL).
    feature_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("features.feature_id"))
    p_home: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    total_mean: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    total_sd: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    over_prob: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    reference_line: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    reference_bookmaker: Mapped[str | None]
    reference_spread: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    market_reference: Mapped[dict[str, Any] | None] = mapped_column(JSONB_NONE_IS_NULL)
    market_status: Mapped[str]
    is_recommendation_basis: Mapped[bool] = mapped_column(server_default=text("false"))
    code_version: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        UniqueConstraint("game_id", "model_version_id", "as_of"),
        CheckConstraint(f"snapshot_kind ~ '{SNAPSHOT_KIND_PATTERN}'", name="snapshot_kind_format"),
        CheckConstraint(ORIGIN_IN, name="origin_valid"),
        CheckConstraint(MARKET_STATUS_IN, name="market_status_valid"),
        open_unit_interval("p_home"),
        open_unit_interval("over_prob"),
        CheckConstraint("total_mean > 0", name="total_mean_positive"),
        CheckConstraint("total_sd > 0", name="total_sd_positive"),
        is_object("market_reference"),
        # A win-only model (Elo) has no total, a total-only model no p_home; one is required.
        CheckConstraint("num_nonnulls(p_home, total_mean) >= 1", name="has_output"),
        CheckConstraint("train_cutoff <= as_of", name="train_cutoff_before_as_of"),
        CheckConstraint("as_of <= computed_at", name="as_of_before_computed"),
        CheckConstraint("as_of < scheduled_start_utc", name="as_of_before_start"),
        CheckConstraint("total_sd IS NULL OR total_mean IS NOT NULL", name="sd_needs_mean"),
        # The over probability is only defined against the stored line (modeling-v1 6-1).
        CheckConstraint("over_prob IS NULL OR reference_line IS NOT NULL", name="over_needs_line"),
        CheckConstraint(
            "reference_line IS NULL OR reference_bookmaker IS NOT NULL", name="line_has_bookmaker"
        ),
        # D-033: one recommendation basis snapshot per game and model.
        Index(
            "uq_predictions_one_recommendation_basis",
            "game_id",
            "model_version_id",
            unique=True,
            postgresql_where=text("is_recommendation_basis"),
        ),
        Index("ix_predictions_as_of", "as_of"),
        Index("ix_predictions_model_as_of", "model_version_id", "as_of"),
    )


class Evaluation(Base):
    """The outcome and per-game metrics of one prediction under one metric version."""

    __tablename__ = "evaluations"

    evaluation_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    prediction_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("predictions.prediction_id"))
    metric_version: Mapped[str]
    home_won: Mapped[bool]
    actual_total: Mapped[int]
    actual_margin: Mapped[int]  # home score - away score
    actual_start_utc: Mapped[datetime]
    rescheduled: Mapped[bool]
    log_loss: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    brier: Mapped[Decimal | None] = mapped_column(Numeric(7, 6))
    correct: Mapped[bool | None]
    abs_error_total: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    sq_error_total: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    over_outcome: Mapped[int | None] = mapped_column(SmallInteger)  # 1 over, 0 under, NULL push
    over_log_loss: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    over_brier: Mapped[Decimal | None] = mapped_column(Numeric(7, 6))
    market_status: Mapped[str]
    market_p_home_pred: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    market_p_home_close: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    market_log_loss_pred: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    market_log_loss_close: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    market_brier_pred: Mapped[Decimal | None] = mapped_column(Numeric(7, 6))
    market_brier_close: Mapped[Decimal | None] = mapped_column(Numeric(7, 6))
    market_total_line_pred: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    market_total_line_close: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    closing_lead_time: Mapped[timedelta | None] = mapped_column(INTERVAL)
    closing_bookmaker: Mapped[str | None]
    line_move: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    clv_prob: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    clv_return: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    beat_close: Mapped[bool | None]
    evaluated_at: Mapped[datetime]
    # games.fetched_at of the result used, so a corrected result can be traced (8-4).
    source_fetched_at: Mapped[datetime]
    code_version: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        UniqueConstraint("prediction_id", "metric_version"),
        nonnegative("actual_total"),
        CheckConstraint("actual_margin <> 0", name="margin_nonzero"),  # NBA games never tie
        CheckConstraint("home_won = (actual_margin > 0)", name="home_won_matches_margin"),
        CheckConstraint("abs(actual_margin) <= actual_total", name="margin_within_total"),
        nonnegative("log_loss"),
        closed_unit_interval("brier"),
        nonnegative("abs_error_total"),
        nonnegative("sq_error_total"),
        CheckConstraint("over_outcome IN (0, 1)", name="over_outcome_valid"),
        nonnegative("over_log_loss"),
        closed_unit_interval("over_brier"),
        CheckConstraint(
            "num_nonnulls(log_loss, brier, correct) IN (0, 3)", name="win_metrics_together"
        ),
        CheckConstraint(
            "num_nonnulls(abs_error_total, sq_error_total) IN (0, 2)",
            name="total_errors_together",
        ),
        CheckConstraint(
            "num_nonnulls(over_log_loss, over_brier) IN (0, 2) "
            "AND (over_log_loss IS NULL OR over_outcome IS NOT NULL)",
            name="over_metrics_together",
        ),
        CheckConstraint(MARKET_STATUS_IN, name="market_status_valid"),
        open_unit_interval("market_p_home_pred"),
        open_unit_interval("market_p_home_close"),
        CheckConstraint("closing_lead_time > interval '0'", name="closing_lead_time_positive"),
        # No market comparison: every market column is NULL and market_status says why (D-026).
        CheckConstraint(
            "market_status IN ('available', 'pred_only') OR num_nonnulls({}) = 0".format(
                ", ".join(MARKET_PRED_COLUMNS + MARKET_CLOSE_COLUMNS + CLOSING_INFO_COLUMNS)
            ),
            name="no_market_all_null",
        ),
        # pred_only: the closing line is outside the lead-time tolerance, so no close or CLV.
        CheckConstraint(
            "market_status = 'available' OR num_nonnulls({}) = 0".format(
                ", ".join(MARKET_CLOSE_COLUMNS)
            ),
            name="close_only_when_available",
        ),
        CheckConstraint("market_status <> 'rescheduled' OR rescheduled", name="rescheduled_status"),
        CheckConstraint(
            "NOT rescheduled OR market_status NOT IN ('available', 'pred_only')",
            name="rescheduled_no_market",
        ),
    )
