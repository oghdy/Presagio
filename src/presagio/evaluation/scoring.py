"""Per-prediction evaluation after the games ended (modeling design v1.2 6-1, 4-4..4-6, 8-4).

Runs after the walk-forward is over, so nothing here can reach a prediction: results, the
closing market and CLV are joined to predictions that were already made. One
EvaluationRecord mirrors one future `evaluations` row (P-031 defines the table, P-033 stores
it). Market fields are None unless the market_status allows the comparison (D-026, 4-6).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import Connection

from presagio.evaluation.clv import Clv, closing_market, game_clv, load_closing_quotes
from presagio.evaluation.data import GameRecord, load_event_matching
from presagio.evaluation.market import (
    AVAILABLE,
    B3_STATUSES,
    NO_ODDS_SEASONS,
    GameMarket,
    market_status,
)
from presagio.evaluation.metrics import (
    game_brier,
    game_correct,
    game_log_loss,
    over_outcome,
)
from presagio.evaluation.timing import FINAL_STATUS
from presagio.evaluation.walkforward import PredictionRecord, WalkForwardResult

# Bumped when a metric definition changes: margin method, closing tolerance, bin count (8-4).
METRIC_VERSION = "mv1"
# Design 1-2: a start time more than this far from the predicted one = rescheduled.
RESCHEDULE_THRESHOLD = timedelta(hours=1)


@dataclass(frozen=True)
class EvaluationRecord:
    """One prediction scored against the result and the market."""

    prediction: PredictionRecord
    home_won: bool
    actual_total: int
    actual_margin: int
    actual_start_utc: datetime
    rescheduled: bool
    log_loss: float
    brier: float
    correct: bool
    abs_error_total: float | None
    sq_error_total: float | None
    over_outcome: int | None  # 1 over, 0 under, None push or no line
    over_log_loss: float | None
    over_brier: float | None
    market_status: str
    market_p_home_pred: float | None
    market_p_home_close: float | None
    market_log_loss_pred: float | None
    market_log_loss_close: float | None
    market_brier_pred: float | None
    market_brier_close: float | None
    market_total_line_pred: float | None
    market_total_line_close: float | None
    closing_lead_time: timedelta | None
    closing_bookmaker: str | None
    line_move: Decimal | None
    clv_h2h: Clv | None  # the model's value side of the moneyline (design 4-5)
    clv_totals: Clv | None
    source_fetched_at: datetime
    metric_version: str = METRIC_VERSION

    @property
    def is_postseason(self) -> bool:
        """Play-in or playoffs (reported together as the postseason, D-049 M-3)."""
        return self.prediction.game.game_type in ("playin", "playoffs")


@dataclass
class ScoringResult:
    """Evaluations of a run and the predictions that could not be scored yet."""

    records: list[EvaluationRecord]
    not_final: int


def score_prediction(
    prediction: PredictionRecord,
    game: GameRecord,
    *,
    close: GameMarket | None,
    has_matched_event: bool,
    has_unmatched_candidate: bool,
    no_odds_seasons: Sequence[str] = NO_ODDS_SEASONS,
) -> EvaluationRecord:
    """Score one prediction of a finished game (labels, model metrics, market, CLV)."""
    assert game.home_score is not None and game.away_score is not None
    y = int(game.home_score > game.away_score)
    total = game.home_score + game.away_score
    p = prediction.p_home
    rescheduled = abs(game.start_time_utc - prediction.game.start_time_utc) > RESCHEDULE_THRESHOLD
    pred_market = prediction.market
    pred_home = pred_market.home
    close_home = close.home if close is not None else None
    status = market_status(
        season=prediction.game.season,
        rescheduled=rescheduled,
        has_prediction_market=pred_home is not None,
        has_closing_market=close_home is not None,
        has_matched_event=has_matched_event,
        has_unmatched_candidate=has_unmatched_candidate,
        no_odds_seasons=no_odds_seasons,
    )
    line = prediction.reference_line
    outcome = None if line is None else over_outcome(total, line)
    over_ok = outcome is not None and prediction.over_prob is not None
    mu = prediction.total_mean
    in_b3 = status in B3_STATUSES
    in_b4 = status == AVAILABLE
    totals_close = None
    line_move = None
    clv_totals = None
    if in_b3 and pred_market.totals_reference is not None:
        ref = pred_market.totals_reference
        close_book = close.totals_books.get(ref.bookmaker_key) if close is not None else None
        if in_b4 and close_book is not None:
            totals_close = float(close_book.point) if close_book.point is not None else None
            if ref.point is not None and close_book.point is not None:
                line_move = close_book.point - ref.point
        if in_b4:
            clv_totals = game_clv("totals", prediction.over_prob, pred_market, close)
    return EvaluationRecord(
        prediction=prediction,
        home_won=bool(y),
        actual_total=total,
        actual_margin=game.home_score - game.away_score,
        actual_start_utc=game.start_time_utc,
        rescheduled=rescheduled,
        log_loss=game_log_loss(p, y),
        brier=game_brier(p, y),
        correct=game_correct(p, y),
        abs_error_total=None if mu is None else abs(mu - total),
        sq_error_total=None if mu is None else (mu - total) ** 2,
        over_outcome=outcome,
        over_log_loss=game_log_loss(prediction.over_prob, outcome) if over_ok else None,
        over_brier=game_brier(prediction.over_prob, outcome) if over_ok else None,
        market_status=status,
        market_p_home_pred=pred_home.p_home if in_b3 and pred_home else None,
        market_p_home_close=close_home.p_home if in_b4 and close_home else None,
        market_log_loss_pred=game_log_loss(pred_home.p_home, y) if in_b3 and pred_home else None,
        market_log_loss_close=game_log_loss(close_home.p_home, y) if in_b4 and close_home else None,
        market_brier_pred=game_brier(pred_home.p_home, y) if in_b3 and pred_home else None,
        market_brier_close=game_brier(close_home.p_home, y) if in_b4 and close_home else None,
        market_total_line_pred=line if in_b3 else None,
        market_total_line_close=totals_close,
        closing_lead_time=close_home.lead_time if in_b4 and close_home else None,
        closing_bookmaker=close_home.source if in_b4 and close_home else None,
        line_move=line_move,
        clv_h2h=game_clv("h2h", p, pred_market, close) if in_b4 else None,
        clv_totals=clv_totals,
        source_fetched_at=game.fetched_at,
    )


def score_run(
    conn: Connection,
    run: WalkForwardResult,
    *,
    no_odds_seasons: Sequence[str] = NO_ODDS_SEASONS,
) -> ScoringResult:
    """Score every prediction of a finished walk-forward whose game is final."""
    config = run.config
    final = [
        p
        for p in run.predictions
        if run.games[p.game.game_id].status_code == FINAL_STATUS
        and run.games[p.game.game_id].home_score is not None
    ]
    ids = sorted({p.game.game_id for p in final})
    closing = load_closing_quotes(conn, ids)
    matched, candidates = load_event_matching(conn, ids)
    records = []
    for prediction in final:
        gid = prediction.game.game_id
        close = closing_market(
            closing.get(gid, []),
            tolerance=config.closing_tolerance,
            method=config.margin_method,
            min_books=config.consensus_min_books,
            order=config.reference_bookmakers,
        )
        records.append(
            score_prediction(
                prediction,
                run.games[gid],
                close=close,
                has_matched_event=gid in matched,
                has_unmatched_candidate=gid in candidates,
                no_odds_seasons=no_odds_seasons,
            )
        )
    return ScoringResult(records, len(run.predictions) - len(final))
