"""Markdown metric tables of a backtest (modeling design v1.2 4-6, 6-1, 6-2).

Segments: season x (regular / postseason), then all requested seasons together. Every segment
has two lines, (a) all games (model only) and (b) games with a market, the same game set for
model and market; (b) is `available + pred_only` against the prediction-time market (B3)
and `available` only against the closing market and CLV (B4). n is always printed and a
market line with n < 100 is marked "reference only". Warm-up predictions (non-learning
baselines before 2024-01-01) are counted but not scored in the tables.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta

from presagio.evaluation.market import (
    AVAILABLE,
    B3_STATUSES,
    B4_STATUSES,
    MARKET_STATUSES,
    RESCHEDULED,
)
from presagio.evaluation.metrics import (
    DEFAULT_BOOTSTRAP,
    DEFAULT_CI_LEVEL,
    DEFAULT_SEED,
    Interval,
    bootstrap_interval,
    bootstrap_mean_interval,
    calibration_table,
    expected_calibration_error,
)
from presagio.evaluation.scoring import METRIC_VERSION, EvaluationRecord, ScoringResult
from presagio.evaluation.store import StoreReport, TableReport
from presagio.evaluation.walkforward import WalkForwardResult

# Design 4-6 (proposal value): market lines with fewer games are "reference only".
SMALL_SAMPLE = 100
ALL = "all"
REGULAR = "regular"
POSTSEASON = "postseason"


@dataclass(frozen=True)
class ReportSettings:
    """Bootstrap and sample-size settings of the tables."""

    n_boot: int = DEFAULT_BOOTSTRAP
    seed: int = DEFAULT_SEED
    level: float = DEFAULT_CI_LEVEL
    small_sample: int = SMALL_SAMPLE


def _fmt(value: float | None, digits: int = 4, signed: bool = False) -> str:
    """A number with fixed decimals, '-' for None."""
    if value is None:
        return "-"
    return f"{value:+.{digits}f}" if signed else f"{value:.{digits}f}"


def _hours(span: timedelta) -> str:
    """'3 h', '24 h', '1.5 h'."""
    return f"{span.total_seconds() / 3600:g} h"


def _fmt_ci(interval: Interval | None, digits: int = 4, signed: bool = False) -> str:
    """'estimate [lower, upper]'."""
    if interval is None:
        return "-"
    return (
        f"{_fmt(interval.estimate, digits, signed)} "
        f"[{_fmt(interval.lower, digits, signed)}, {_fmt(interval.upper, digits, signed)}]"
    )


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    """Markdown table lines."""
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(" --- " for _ in headers) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return lines


def _segments(
    records: Sequence[EvaluationRecord], seasons: Sequence[str]
) -> list[tuple[str, str, list[EvaluationRecord]]]:
    """(season, phase, records) for each non-empty season x phase, then all seasons."""
    out = []
    for season in seasons:
        for phase in (REGULAR, POSTSEASON):
            chosen = [
                r
                for r in records
                if r.prediction.game.season == season and r.is_postseason == (phase == POSTSEASON)
            ]
            if chosen:
                out.append((season, phase, chosen))
    if len({s for s, _, _ in out}) > 1 or len(out) > 1:
        out.append((ALL, ALL, list(records)))
    return out


def _mean_ci(values: Sequence[float], s: ReportSettings) -> Interval | None:
    """Bootstrap interval of a mean, None without values."""
    if not values:
        return None
    return bootstrap_mean_interval(values, n_boot=s.n_boot, seed=s.seed, level=s.level)


def _paired_ci(
    a: Sequence[float], b: Sequence[float], stat: Callable[..., object], s: ReportSettings
) -> Interval | None:
    """Bootstrap interval of a paired statistic of two per-game columns."""
    if not a:
        return None
    return bootstrap_interval([a, b], stat, n_boot=s.n_boot, seed=s.seed, level=s.level)


def _delta(model: object, reference: object) -> object:
    """model - reference (works on floats and numpy arrays)."""
    return model - reference  # type: ignore[operator]


def _skill(model: object, reference: object) -> object:
    """1 - model / reference: the Brier skill score."""
    return 1 - model / reference  # type: ignore[operator]


def _status_note(
    records: Sequence[EvaluationRecord], comparable: frozenset[str] = B3_STATUSES
) -> str:
    """'no market: no_odds_season 1230' style note from the status counts."""
    counts = Counter(r.market_status for r in records if r.market_status not in comparable)
    return "no market: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))


def model_rows(
    segments: Sequence[tuple[str, str, list[EvaluationRecord]]], s: ReportSettings
) -> list[list[str]]:
    """Table (a)/(b) rows of model metrics."""
    rows = []
    for season, phase, records in segments:
        market = [r for r in records if r.market_status in B3_STATUSES]
        for label, chosen in (("(a) all games", records), ("(b) with market", market)):
            if not chosen:
                rows.append([season, phase, label, "0", *["-"] * 6, _status_note(records)])
                continue
            ps = [r.prediction.p_home for r in chosen]
            ys = [int(r.home_won) for r in chosen]
            totals = [r for r in chosen if r.abs_error_total is not None]
            rmse = (sum(r.sq_error_total for r in totals) / len(totals)) ** 0.5 if totals else None
            note = ""
            if label.startswith("(b)") and len(chosen) < s.small_sample:
                note = f"reference only (n < {s.small_sample})"
            rows.append(
                [
                    season,
                    phase,
                    label,
                    str(len(chosen)),
                    _fmt(sum(r.correct for r in chosen) / len(chosen), 3),
                    _fmt_ci(_mean_ci([r.log_loss for r in chosen], s)),
                    _fmt_ci(_mean_ci([r.brier for r in chosen], s)),
                    _fmt(expected_calibration_error(calibration_table(ps, ys))),
                    _fmt_ci(_mean_ci([r.abs_error_total for r in totals], s), 2),
                    _fmt(rmse, 2),
                    note,
                ]
            )
    return rows


def _market_row(
    season: str,
    phase: str,
    segment: Sequence[EvaluationRecord],
    comparable: frozenset[str],
    pick: Callable[[EvaluationRecord], tuple[float | None, float | None]],
    s: ReportSettings,
) -> list[str]:
    """One model-vs-market row on the segment's comparable games (same set for both)."""
    chosen = [r for r in segment if r.market_status in comparable]
    pairs = [(r, *pick(r)) for r in chosen]
    pairs = [(r, ll, br) for r, ll, br in pairs if ll is not None and br is not None]
    n = len(pairs)
    if n == 0:
        return [season, phase, "0", *["-"] * 6, _status_note(segment, comparable)]
    model_ll = [r.log_loss for r, _, _ in pairs]
    market_ll = [ll for _, ll, _ in pairs]
    model_br = [r.brier for r, _, _ in pairs]
    market_br = [br for _, _, br in pairs]
    note = f"reference only (n < {s.small_sample})" if n < s.small_sample else ""
    return [
        season,
        phase,
        str(n),
        _fmt(sum(model_ll) / n),
        _fmt(sum(market_ll) / n),
        _fmt_ci(_paired_ci(model_ll, market_ll, _delta, s), signed=True),
        _fmt(sum(model_br) / n),
        _fmt_ci(_paired_ci(model_br, market_br, _delta, s), signed=True),
        _fmt_ci(_paired_ci(model_br, market_br, _skill, s), signed=True),
        note,
    ]


def _clv_cells(chosen: Sequence[EvaluationRecord], s: ReportSettings) -> list[str]:
    """Mean clv_return [CI], beat_close share and n of the h2h value side."""
    clvs = [r.clv_h2h for r in chosen if r.clv_h2h is not None and r.clv_h2h.clv_return is not None]
    if not clvs:
        return ["0", "-", "-"]
    beat = [c for c in clvs if c.beat_close is not None]
    return [
        str(len(clvs)),
        _fmt_ci(_mean_ci([c.clv_return for c in clvs], s), signed=True),
        _fmt(sum(c.beat_close for c in beat) / len(beat), 3) if beat else "-",
    ]


def _lead_buckets(chosen: Sequence[EvaluationRecord]) -> str:
    """Closing lead time counts: <= 30 min / <= 1 h / <= 2 h."""
    leads = [r.closing_lead_time for r in chosen if r.closing_lead_time is not None]
    buckets = (timedelta(minutes=30), timedelta(hours=1), timedelta(hours=2))
    return " / ".join(str(sum(1 for x in leads if x <= b)) for b in buckets)


def _table_line(name: str, t: TableReport) -> str:
    """'predictions inserted 10, already stored 0, differing 0'."""
    return f"{name} inserted {t.inserted}, already stored {t.existing}, differing {t.mismatched}"


def storage_lines(store: StoreReport | None, dry_run: bool) -> list[str]:
    """What the run wrote (or did not write) to the database."""
    if store is None:
        return [
            "- "
            + ("Dry run: " if dry_run else "")
            + "nothing was written to the database"
            + ("." if dry_run else " (pass --store to write predictions and evaluations).")
        ]
    lines = [
        f"- Stored (--store): model_versions {store.model_name} v{store.version} "
        f"(model_version_id {store.model_version_id}, "
        f"{'new' if store.model_version_new else 'existing'}"
        + (
            f", holdout unlock recorded: {store.holdout_reason_recorded}"
            if store.holdout_reason_recorded
            else ""
        )
        + f"); {_table_line('predictions', store.predictions)}; "
        f"{_table_line('evaluations', store.evaluations)}; holdout rows not written: "
        f"{store.holdout_skipped_predictions} predictions, "
        f"{store.holdout_skipped_evaluations} evaluations."
    ]
    if store.mismatches:
        lines.append(
            f"- WARNING: {store.mismatches} stored row(s) differ from this run and were NOT "
            "overwritten (determinism check, design 8-3; bump the model version if the "
            "change is intended). Examples:"
        )
        lines += [f"  - {e}" for e in store.predictions.examples + store.evaluations.examples]
    return lines


def _not_predicted_note(run: WalkForwardResult) -> str:
    """market_status counts of the games the predictor returned nothing for."""
    if not run.not_predicted:
        return ""
    counts = Counter(n.market_status for n in run.not_predicted)
    return (
        " (not predicted by market_status: "
        + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
        + ")"
    )


def render_report(
    run: WalkForwardResult,
    scoring: ScoringResult,
    *,
    experiment: str,
    settings: ReportSettings | None = None,
    dry_run: bool = False,
    store: StoreReport | None = None,
) -> str:
    """The full markdown report of one backtest run."""
    s = settings or ReportSettings()
    cfg = run.config
    reported = [r for r in scoring.records if r.prediction.reported]
    warmup = sum(1 for r in scoring.records if not r.prediction.reported)
    holdout = run.holdout
    lines = [
        f"# Backtest {experiment}: {run.predictor_name}",
        "",
        f"- Seasons: {', '.join(cfg.seasons)}. Holdout "
        + (
            f"UNLOCKED for {', '.join(holdout.holdout_seasons)}: {holdout.unlocked_reason}"
            if holdout.unlocked
            else f"locked ({', '.join(cfg.holdout_seasons)} not evaluated)"
        ),
        f"- as_of: each US Eastern date 07:00 UTC (snapshot_kind daily, origin backtest). "
        "Results usable when D(h) < D(g) and "
        f"start + {_hours(cfg.availability_lag)} <= as_of (R1, R2). "
        f"Learning models refit every {cfg.refit_every_days} days (expanding), first "
        f"prediction after {cfg.min_train_season}; baselines reported from "
        f"{cfg.baseline_report_from}.",
        f"- Market: {cfg.margin_method} margin removal; consensus median of >= "
        f"{cfg.consensus_min_books} books, else {' > '.join(cfg.reference_bookmakers)}; "
        f"prediction-time quotes captured_at <= as_of within {_hours(cfg.max_market_age)} (R3); "
        f"closing = odds_closing_lines with lead_time <= {_hours(cfg.closing_tolerance)}.",
        f"- Uncertainty: game-level bootstrap, {s.n_boot} resamples, seed {s.seed}, "
        f"{s.level:.0%} percentile interval. metric_version {METRIC_VERSION}.",
        f"- Predictions: {len(run.predictions)} on {run.days} days; scored {len(reported)}; "
        f"warm-up (not reported) {warmup}; not final yet {scoring.not_final}; not predicted: "
        + (", ".join(f"{k} {v}" for k, v in sorted(run.skipped.items())) or "none")
        + _not_predicted_note(run)
        + ".",
        *storage_lines(store, dry_run),
        "",
    ]
    if not reported:
        lines.append("No scored predictions.")
        return "\n".join(lines) + "\n"
    segments = _segments(reported, cfg.seasons)
    lines += ["## Model metrics (lower is better except accuracy)", ""]
    lines += _table(
        (
            "season",
            "phase",
            "set",
            "n",
            "accuracy",
            "log loss [90%]",
            "Brier [90%]",
            "ECE",
            "total MAE [90%]",
            "total RMSE",
            "note",
        ),
        model_rows(segments, s),
    )
    lines += [
        "",
        "## vs prediction-time market B3 (market_status available + pred_only)",
        "",
    ]
    b3_rows = [
        _market_row(
            season,
            phase,
            chosen,
            B3_STATUSES,
            lambda r: (r.market_log_loss_pred, r.market_brier_pred),
            s,
        )
        for season, phase, chosen in segments
    ]
    market_headers = (
        "season",
        "phase",
        "n",
        "LL model",
        "LL market",
        "dLL [90%]",
        "Brier model",
        "dBrier [90%]",
        "BSS [90%]",
        "note",
    )
    lines += _table(market_headers, b3_rows)
    lines += ["", "## vs closing market B4 and CLV (market_status available)", ""]
    b4_rows = []
    for season, phase, chosen in segments:
        available = [r for r in chosen if r.market_status == AVAILABLE]
        row = _market_row(
            season,
            phase,
            chosen,
            B4_STATUSES,
            lambda r: (r.market_log_loss_close, r.market_brier_close),
            s,
        )
        b4_rows.append([*row[:-1], *_clv_cells(available, s), _lead_buckets(available), row[-1]])
    lines += _table(
        (
            *market_headers[:-1],
            "CLV n",
            "mean clv_return [90%]",
            "beat_close",
            "lead <=30m / <=1h / <=2h",
            "note",
        ),
        b4_rows,
    )
    lines += ["", "## market_status counts", ""]
    lines += _table(
        ("season", "phase", "n", *MARKET_STATUSES),
        [
            [
                season,
                phase,
                str(len(chosen)),
                *(
                    str(sum(1 for r in chosen if r.market_status == status))
                    for status in MARKET_STATUSES
                ),
            ]
            for season, phase, chosen in segments
        ],
    )
    if any(r.market_status == RESCHEDULED for r in reported):
        lines.append("Rescheduled games (> 1 h from the predicted start) leave every market line.")
    over = [r for r in reported if r.over_log_loss is not None]
    pushes = sum(
        1
        for r in reported
        if r.prediction.reference_line is not None
        and r.prediction.over_prob is not None
        and r.over_outcome is None
    )
    lines += ["", "## Over probability (push excluded)", ""]
    if over:
        lines += _table(
            ("n", "pushes", "log loss [90%]", "Brier [90%]"),
            [
                [
                    str(len(over)),
                    str(pushes),
                    _fmt_ci(_mean_ci([r.over_log_loss for r in over], s)),
                    _fmt_ci(_mean_ci([r.over_brier for r in over], s)),
                ]
            ],
        )
    else:
        lines.append("No over probabilities with a reference line.")
    lines += ["", "## Calibration (all scored games, 10 equal bins)", ""]
    table = calibration_table(
        [r.prediction.p_home for r in reported], [int(r.home_won) for r in reported]
    )
    lines += _table(
        ("bin", "n", "mean p", "home win rate"),
        [
            [
                f"[{b.lower:.1f}, {b.upper:.1f}{']' if b.upper == 1 else ')'}",
                str(b.n),
                _fmt(b.mean_p),
                _fmt(b.observed),
            ]
            for b in table
        ],
    )
    lines.append(f"ECE {expected_calibration_error(table):.4f}")
    return "\n".join(lines) + "\n"
