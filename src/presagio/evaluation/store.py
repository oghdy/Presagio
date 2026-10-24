"""Storage of a backtest run: model_versions, predictions, evaluations (schema v1 ch. 9).

Rules (schema v1 9-1, modeling design 8-2..8-4, D-030):

- Append only. predictions and evaluations are inserted with INSERT ... ON CONFLICT DO
  NOTHING; a row that already exists under the same key is never updated. Its stored values
  are compared with the values this run produced and every difference is reported with a
  count and examples (the same way the feature_drift quality check reports), never written.
  An evaluation is never attached to a prediction whose stored values differ from this
  run's (it would score the stored prediction with this run's numbers, D-055 item 2); those
  evaluations are counted separately.
- One model_versions row per (model_name, version). An existing row must describe the same
  configuration (family, feature version, hyperparameters, seed, training window, tuning
  data, experiment); otherwise nothing is stored (StoreError: bump the version). Only
  holdout_unlocked_reason may change on an existing row, from NULL to the run's reason.
- Holdout seasons (D-049 M-2): predictions and evaluations of a locked holdout season are
  never written, whatever the run produced; an unlocked run records the reason.
- Probabilities are clipped to [0.0001, 0.9999] and every numeric value is quantized to its
  column's scale before insert and before comparison (schema v1 9-1 item 8).
- market_reference is a deep copy of the prediction's market summary (P-032 review (c)).
"""

from __future__ import annotations

import copy
import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import Connection, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from presagio.db.models.modeling import MODEL_FAMILIES, Evaluation, ModelVersion, Prediction
from presagio.evaluation.scoring import EvaluationRecord, ScoringResult
from presagio.evaluation.walkforward import PredictionRecord, WalkForwardResult
from presagio.ingestion.runs import current_code_version
from presagio.models.spec import ModelSpec

logger = logging.getLogger("presagio.evaluation.store")

# Schema v1 9-1 item 8: numeric(6,4) probabilities stay inside the open interval (0, 1).
PROB_MIN = Decimal("0.0001")
PROB_MAX = Decimal("0.9999")
INSERT_CHUNK = 500
MAX_EXAMPLES = 5

PREDICTION_COMPARED = (
    "snapshot_kind",
    "origin",
    "train_cutoff",
    "scheduled_start_utc",
    "feature_id",
    "p_home",
    "total_mean",
    "total_sd",
    "over_prob",
    "reference_line",
    "reference_bookmaker",
    "reference_spread",
    "market_reference",
    "market_status",
    "is_recommendation_basis",
)
EVALUATION_COMPARED = (
    "home_won",
    "actual_total",
    "actual_margin",
    "actual_start_utc",
    "rescheduled",
    "log_loss",
    "brier",
    "correct",
    "abs_error_total",
    "sq_error_total",
    "over_outcome",
    "over_log_loss",
    "over_brier",
    "market_status",
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
)
MODEL_VERSION_COMPARED = (
    "model_family",
    "feature_version",
    "hyperparameters",
    "seed",
    "training_window",
    "tuning_data",
    "experiment_id",
    "holdout_seasons",
)


class StoreError(RuntimeError):
    """The run cannot be stored as it is (configuration conflict, no code version)."""


@dataclass
class TableReport:
    """What happened to one table's rows."""

    inserted: int = 0
    existing: int = 0  # same key, same values
    mismatched: int = 0  # same key, different values: reported, not written
    examples: list[str] = field(default_factory=list)
    mismatched_keys: set[tuple] = field(default_factory=set, repr=False)

    @property
    def total(self) -> int:
        """Rows this run tried to store."""
        return self.inserted + self.existing + self.mismatched


@dataclass
class StoreReport:
    """Outcome of store_run (for the markdown report and the exit code)."""

    model_version_id: int
    model_name: str
    version: str
    model_version_new: bool
    holdout_reason_recorded: str | None
    predictions: TableReport
    evaluations: TableReport
    holdout_skipped_predictions: int
    holdout_skipped_evaluations: int
    # Evaluations not written because their prediction's stored values differ (D-055 item 2).
    evaluations_skipped_for_mismatch: int = 0

    @property
    def mismatches(self) -> int:
        """Rows whose stored values differ from this run's (determinism check, 8-3)."""
        return self.predictions.mismatched + self.evaluations.mismatched


def quantize(value: float | Decimal | None, places: int) -> Decimal | None:
    """value as a Decimal with `places` decimals (ROUND_HALF_UP, like PostgreSQL), or None."""
    if value is None:
        return None
    raw = value if isinstance(value, Decimal) else Decimal(repr(float(value)))
    return raw.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def probability(value: float | Decimal | None) -> Decimal | None:
    """A numeric(6,4) probability inside (0, 1): clipped to [0.0001, 0.9999], 4 decimals."""
    q = quantize(value, 4)
    return None if q is None else min(max(q, PROB_MIN), PROB_MAX)


def _json_copy(obj: Any) -> Any:
    """A detached, JSON-normalised copy (tuples become lists, Decimals strings)."""
    return json.loads(json.dumps(copy.deepcopy(obj), default=str))


def training_window(run: WalkForwardResult, predictor: object) -> dict[str, Any]:
    """model_versions.training_window: the runner's information rules of this configuration
    (modeling design 8-2: train_start, refit cadence, minimum training; plus R2/R3 settings).
    The requested seasons are a run parameter, not part of the configuration."""
    cfg = run.config
    trains = bool(getattr(predictor, "trains", False))
    return {
        "window": "expanding",
        "train_start": "first requested season (D-049 M-6)",
        "refit_every_days": cfg.refit_every_days if trains else None,
        "refit": "every refit_every_days" if trains else "none (sequential daily update)",
        "min_train_rule": (
            f"first prediction after season {cfg.min_train_season}"
            if trains
            else f"predicts from the first requested season, reported from "
            f"{cfg.baseline_report_from.isoformat()}"
        ),
        "as_of": "US Eastern date 07:00 UTC (daily, backtest; D-049 M-1)",
        "availability_lag_hours": cfg.availability_lag.total_seconds() / 3600,
        "market": {
            "margin_method": cfg.margin_method,
            "consensus_min_books": cfg.consensus_min_books,
            "reference_bookmakers": list(cfg.reference_bookmakers),
            "max_market_age_hours": cfg.max_market_age.total_seconds() / 3600,
        },
    }


def _differences(
    stored: Mapping[str, Any], new: Mapping[str, Any], columns: Iterable[str]
) -> list[str]:
    """'column: stored -> new' for every compared column whose values differ."""
    return [
        f"{c}: {stored.get(c)!r} -> {new.get(c)!r}" for c in columns if stored.get(c) != new.get(c)
    ]


def _store_model_version(
    conn: Connection, row: dict[str, Any], unlocked_reason: str | None
) -> tuple[int, bool, str | None]:
    """Insert the configuration or verify the existing row; (id, inserted, reason recorded)."""
    table = ModelVersion.__table__
    stmt = (
        pg_insert(table)
        .values(row)
        .on_conflict_do_nothing(index_elements=["model_name", "version"])
        .returning(table.c.model_version_id)
    )
    inserted_id = conn.execute(stmt).scalar()
    if inserted_id is not None:
        return inserted_id, True, unlocked_reason
    stored = (
        conn.execute(
            select(table).where(
                table.c.model_name == row["model_name"], table.c.version == row["version"]
            )
        )
        .mappings()
        .one()
    )
    new = {**row, "hyperparameters": _json_copy(row["hyperparameters"])}
    new["training_window"] = _json_copy(row["training_window"])
    diffs = _differences(stored, new, MODEL_VERSION_COMPARED)
    if diffs:
        raise StoreError(
            f"model_versions {row['model_name']} v{row['version']} already exists with a "
            f"different configuration ({'; '.join(diffs)}); bump the version instead of "
            f"overwriting (schema v1 9-1)"
        )
    recorded = None
    if unlocked_reason is not None:
        if stored["holdout_unlocked_reason"] is None:
            conn.execute(
                update(table)
                .where(table.c.model_version_id == stored["model_version_id"])
                .values(holdout_unlocked_reason=unlocked_reason)
            )
            recorded = unlocked_reason
        elif stored["holdout_unlocked_reason"] != unlocked_reason:
            raise StoreError(
                f"model_versions {row['model_name']} v{row['version']} was already unlocked "
                f"for the holdout with another reason "
                f"({stored['holdout_unlocked_reason']!r}); the holdout opens once (6-3)"
            )
    return stored["model_version_id"], False, recorded


def prediction_row(
    p: PredictionRecord, model_version_id: int, computed_at: datetime, code_version: str
) -> dict[str, Any]:
    """The predictions row of one PredictionRecord (values quantized, market copied)."""
    has_market = p.market.home is not None
    return {
        "game_id": p.game.game_id,
        "model_version_id": model_version_id,
        "as_of": p.as_of,
        "snapshot_kind": p.snapshot_kind,
        "origin": p.origin,
        "computed_at": computed_at,
        "train_cutoff": p.train_cutoff,
        "scheduled_start_utc": p.game.start_time_utc,
        "feature_id": None,  # no feature rows before P-035
        "p_home": probability(p.p_home),
        "total_mean": quantize(p.total_mean, 2),
        "total_sd": quantize(p.total_sd, 2),
        "over_prob": probability(p.over_prob),
        "reference_line": quantize(p.reference_line, 2),
        "reference_bookmaker": p.reference_bookmaker,
        "reference_spread": None,  # stage 4
        "market_reference": _json_copy(p.market.reference()) if has_market else None,
        "market_status": p.market_status,
        "is_recommendation_basis": False,  # no recommendations before stage 4
        "code_version": code_version,
    }


def evaluation_row(
    r: EvaluationRecord, prediction_id: int, evaluated_at: datetime, code_version: str
) -> dict[str, Any]:
    """The evaluations row of one EvaluationRecord (values quantized)."""
    clv = r.clv_h2h
    return {
        "prediction_id": prediction_id,
        "metric_version": r.metric_version,
        "home_won": r.home_won,
        "actual_total": r.actual_total,
        "actual_margin": r.actual_margin,
        "actual_start_utc": r.actual_start_utc,
        "rescheduled": r.rescheduled,
        "log_loss": quantize(r.log_loss, 6),
        "brier": quantize(r.brier, 6),
        "correct": r.correct,
        "abs_error_total": quantize(r.abs_error_total, 2),
        "sq_error_total": quantize(r.sq_error_total, 2),
        "over_outcome": r.over_outcome,
        "over_log_loss": quantize(r.over_log_loss, 6),
        "over_brier": quantize(r.over_brier, 6),
        "market_status": r.market_status,
        "market_p_home_pred": probability(r.market_p_home_pred),
        "market_p_home_close": probability(r.market_p_home_close),
        "market_log_loss_pred": quantize(r.market_log_loss_pred, 6),
        "market_log_loss_close": quantize(r.market_log_loss_close, 6),
        "market_brier_pred": quantize(r.market_brier_pred, 6),
        "market_brier_close": quantize(r.market_brier_close, 6),
        "market_total_line_pred": quantize(r.market_total_line_pred, 2),
        "market_total_line_close": quantize(r.market_total_line_close, 2),
        "closing_lead_time": r.closing_lead_time,
        "closing_bookmaker": r.closing_bookmaker,
        "line_move": quantize(r.line_move, 2),
        "clv_prob": quantize(clv.clv_prob, 4) if clv else None,
        "clv_return": quantize(clv.clv_return, 4) if clv else None,
        "beat_close": clv.beat_close if clv else None,
        "evaluated_at": evaluated_at,
        "source_fetched_at": r.source_fetched_at,
        "code_version": code_version,
    }


def _chunks(rows: Sequence[dict[str, Any]], size: int = INSERT_CHUNK) -> Iterable[Sequence[dict]]:
    """Row batches small enough for one multi-row INSERT."""
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def _insert_or_ignore(
    conn: Connection, table: Any, rows: Sequence[dict[str, Any]], key: Sequence[str]
) -> set[tuple]:
    """INSERT ... ON CONFLICT DO NOTHING; the keys of the rows that were inserted."""
    inserted: set[tuple] = set()
    for chunk in _chunks(rows):
        stmt = (
            pg_insert(table)
            .values(list(chunk))
            .on_conflict_do_nothing(index_elements=list(key))
            .returning(*(table.c[k] for k in key))
        )
        inserted.update(tuple(row) for row in conn.execute(stmt))
    return inserted


def _compare(
    rows: Sequence[dict[str, Any]],
    stored_by_key: Mapping[tuple, Mapping[str, Any]],
    key: Sequence[str],
    inserted: set[tuple],
    columns: Sequence[str],
    label: str,
) -> TableReport:
    """Count inserted / existing-equal / mismatched rows, with examples of the differences."""
    report = TableReport()
    for row in rows:
        k = tuple(row[c] for c in key)
        if k in inserted:
            report.inserted += 1
            continue
        stored = stored_by_key[k]
        diffs = _differences(stored, row, columns)
        if not diffs:
            report.existing += 1
            continue
        report.mismatched += 1
        report.mismatched_keys.add(k)
        if len(report.examples) < MAX_EXAMPLES:
            report.examples.append(f"{label} {k}: " + "; ".join(diffs))
    return report


def store_run(
    conn: Connection,
    run: WalkForwardResult,
    scoring: ScoringResult,
    predictor: object,
    *,
    experiment_id: str,
    seed: int,
    now: datetime,
    code_version: str | None = None,
) -> StoreReport:
    """Store a finished, scored run in one transaction (the caller's): the configuration,
    every prediction and every evaluation, skipping locked holdout seasons."""
    spec = getattr(predictor, "spec", None)
    if not isinstance(spec, ModelSpec):
        raise StoreError(f"experiment {experiment_id} has no ModelSpec; it cannot be stored")
    if spec.model_family not in MODEL_FAMILIES:
        raise StoreError(f"unknown model_family {spec.model_family!r}")
    code_version = code_version or current_code_version()
    if code_version is None:
        raise StoreError("code_version is unknown (no git checkout, no installed package)")
    cfg = run.config
    locked = set(cfg.holdout_seasons)
    if run.holdout.unlocked:
        locked -= set(run.holdout.holdout_seasons)
    model_row = {
        "model_name": spec.model_name,
        "version": spec.version,
        "model_family": spec.model_family,
        "feature_version": spec.feature_version,
        "hyperparameters": _json_copy(dict(spec.hyperparameters)),
        "seed": seed,
        "code_version": code_version,
        "training_window": training_window(run, predictor),
        "tuning_data": spec.tuning_data,
        "experiment_id": experiment_id,
        "holdout_seasons": list(cfg.holdout_seasons),
        "holdout_unlocked_reason": run.holdout.unlocked_reason,
    }
    model_version_id, is_new, recorded = _store_model_version(
        conn, model_row, run.holdout.unlocked_reason
    )
    kept = [p for p in run.predictions if p.game.season not in locked]
    pred_rows = [prediction_row(p, model_version_id, now, code_version) for p in kept]
    for row in pred_rows:
        if not row["as_of"] < row["scheduled_start_utc"]:  # R4, also a DB CHECK
            raise StoreError(f"prediction {row['game_id']} is not before its tip-off")
    pred_key = ("game_id", "model_version_id", "as_of")
    pred_table = Prediction.__table__
    inserted = _insert_or_ignore(conn, pred_table, pred_rows, pred_key)
    game_ids = sorted({row["game_id"] for row in pred_rows})
    stored_preds = {
        (r["game_id"], r["model_version_id"], r["as_of"]): r
        for r in conn.execute(
            select(pred_table).where(
                pred_table.c.model_version_id == model_version_id,
                pred_table.c.game_id.in_(game_ids),
            )
        ).mappings()
    }
    pred_report = _compare(
        pred_rows, stored_preds, pred_key, inserted, PREDICTION_COMPARED, "prediction"
    )
    scored = [r for r in scoring.records if r.prediction.game.season not in locked]
    eval_rows = []
    skipped_for_mismatch = 0
    for r in scored:
        key = (r.prediction.game.game_id, model_version_id, r.prediction.as_of)
        if key not in stored_preds:
            raise StoreError(f"evaluation of a prediction this run did not make: {key}")
        if key in pred_report.mismatched_keys:
            skipped_for_mismatch += 1  # never score a stored prediction with this run's values
            continue
        eval_rows.append(evaluation_row(r, stored_preds[key]["prediction_id"], now, code_version))
    eval_key = ("prediction_id", "metric_version")
    eval_table = Evaluation.__table__
    inserted_evals = _insert_or_ignore(conn, eval_table, eval_rows, eval_key)
    stored_evals = {
        (r["prediction_id"], r["metric_version"]): r
        for r in conn.execute(
            select(eval_table).where(
                eval_table.c.prediction_id.in_([row["prediction_id"] for row in eval_rows])
            )
        ).mappings()
    }
    eval_report = _compare(
        eval_rows, stored_evals, eval_key, inserted_evals, EVALUATION_COMPARED, "evaluation"
    )
    report = StoreReport(
        model_version_id=model_version_id,
        model_name=spec.model_name,
        version=spec.version,
        model_version_new=is_new,
        holdout_reason_recorded=recorded,
        predictions=pred_report,
        evaluations=eval_report,
        holdout_skipped_predictions=len(run.predictions) - len(kept),
        holdout_skipped_evaluations=len(scoring.records) - len(scored),
        evaluations_skipped_for_mismatch=skipped_for_mismatch,
    )
    logger.info(
        "stored %s v%s (model_version_id %d%s): predictions +%d =%d !%d, evaluations +%d =%d "
        "!%d (not written for differing predictions %d), holdout skipped %d/%d",
        spec.model_name,
        spec.version,
        model_version_id,
        ", new" if is_new else "",
        pred_report.inserted,
        pred_report.existing,
        pred_report.mismatched,
        eval_report.inserted,
        eval_report.existing,
        eval_report.mismatched,
        skipped_for_mismatch,
        report.holdout_skipped_predictions,
        report.holdout_skipped_evaluations,
    )
    for example in pred_report.examples + eval_report.examples:
        logger.warning("stored value differs (not overwritten): %s", example)
    return report
