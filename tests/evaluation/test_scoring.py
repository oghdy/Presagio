"""Scoring after the run: results, market_status, closing market, CLV (design 4-4..4-6, 8-4)."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import Connection

from presagio.evaluation.dummy import ConstantPredictor
from presagio.evaluation.scoring import EvaluationRecord, ScoringResult, score_prediction, score_run
from presagio.evaluation.walkforward import WalkForwardConfig, WalkForwardResult, run_walk_forward

from .league import LIVE_AVAILABLE, LIVE_PRED_ONLY, LIVE_UNMATCHED, League

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def live_run(conn: Connection) -> tuple[WalkForwardResult, ScoringResult]:
    """The constant dummy over 2026-27, scored."""
    run = run_walk_forward(
        conn, ConstantPredictor(), WalkForwardConfig(seasons=("2026-27",)), now=NOW
    )
    return run, score_run(conn, run)


def by_game(scoring: ScoringResult) -> dict[str, EvaluationRecord]:
    """Records keyed by game id."""
    return {r.prediction.game.game_id: r for r in scoring.records}


def test_market_status_from_the_database(conn: Connection) -> None:
    """10-20 unmatched event -> unmatched_event, 10-21 -> available, 10-22 -> pred_only (close
    21 h before tip-off), 10-23 not final -> not scored."""
    _, scoring = live_run(conn)
    records = by_game(scoring)
    assert {gid: r.market_status for gid, r in records.items()} == {
        LIVE_UNMATCHED: "unmatched_event",
        LIVE_AVAILABLE: "available",
        LIVE_PRED_ONLY: "pred_only",
    }
    assert scoring.not_final == 1


def test_available_game_by_hand(conn: Connection, league: League) -> None:
    """10-21 (away won 122-110). Prediction-time consensus = median(0.461538 dk, 0.471698 fd,
    0.448718 mgm) = 0.461538; close consensus = median(0.512821, 0.5, 0.507692) = 0.507692,
    lead 45 min. Market LL pred = -ln(0.538462) = 0.619039. Totals line 220.5 -> 222.5:
    line_move +2. The constant model (0.5) values home at draftkings 2.10: clv_return =
    2.10 x 0.512821 - 1 = 0.076923, beat_close (2.10 > 1.90)."""
    _, scoring = live_run(conn)
    r = by_game(scoring)[LIVE_AVAILABLE]
    game = league.games[LIVE_AVAILABLE]
    assert (game["home_score"], game["away_score"]) == (110, 122)
    assert r.home_won is False and r.actual_total == 232 and r.actual_margin == -12
    assert r.log_loss == pytest.approx(math.log(2)) and r.brier == pytest.approx(0.25)
    assert r.correct is False
    assert r.market_p_home_pred == pytest.approx(0.4615384615)
    assert r.market_p_home_close == pytest.approx((1 / 1.92) / (1 / 1.92 + 1 / 1.98))
    assert r.market_log_loss_pred == pytest.approx(-math.log(1 - 0.4615384615))
    assert r.market_brier_close == pytest.approx(r.market_p_home_close**2)
    assert r.closing_lead_time == timedelta(minutes=45) and r.closing_bookmaker == "consensus"
    assert r.market_total_line_pred == 220.5 and r.market_total_line_close == 222.5
    assert r.line_move == Decimal("2.00")
    assert r.prediction.reference_line == 220.5
    assert r.over_outcome == 1 and r.over_log_loss is None  # dummy has no over probability
    clv = r.clv_h2h
    assert clv is not None and (clv.side, clv.bookmaker_key) == ("home", "draftkings")
    assert clv.clv_return == pytest.approx(0.0769230769)
    assert clv.beat_close is True
    assert r.clv_totals is None
    assert r.metric_version == "mv1"


def test_pred_only_and_unmatched_have_no_closing_fields(conn: Connection) -> None:
    """pred_only keeps the prediction-time market and drops close and CLV; unmatched keeps
    nothing (D-026: NULL plus a reason)."""
    _, scoring = live_run(conn)
    records = by_game(scoring)
    pred_only = records[LIVE_PRED_ONLY]
    assert pred_only.market_p_home_pred == pytest.approx(0.5)
    assert pred_only.market_p_home_close is None and pred_only.clv_h2h is None
    assert pred_only.closing_lead_time is None
    unmatched = records[LIVE_UNMATCHED]
    assert unmatched.market_p_home_pred is None and unmatched.market_log_loss_pred is None
    assert unmatched.market_total_line_pred is None


def test_past_seasons_are_no_odds_season(conn: Connection) -> None:
    """2024-25 has no odds (D-026): every game no_odds_season with NULL market fields."""
    run = run_walk_forward(
        conn, ConstantPredictor(), WalkForwardConfig(seasons=("2024-25",)), now=NOW
    )
    scoring = score_run(conn, run)
    assert {r.market_status for r in scoring.records} == {"no_odds_season"}
    assert all(r.market_p_home_pred is None for r in scoring.records)
    assert len(scoring.records) == 171


def test_rescheduled_game_leaves_the_market_comparison(conn: Connection, league: League) -> None:
    """A game that started more than 1 hour from the predicted start is `rescheduled`."""
    run, _ = live_run(conn)
    prediction = next(p for p in run.predictions if p.game.game_id == LIVE_AVAILABLE)
    moved = replace(
        prediction,
        game=replace(
            prediction.game,
            start_time_utc=prediction.game.start_time_utc - timedelta(hours=1, minutes=1),
        ),
    )
    record = score_prediction(
        moved,
        run.games[LIVE_AVAILABLE],
        close=None,
        has_matched_event=True,
        has_unmatched_candidate=False,
    )
    assert record.rescheduled and record.market_status == "rescheduled"
    assert record.market_p_home_pred is None and record.clv_h2h is None
    on_time = replace(
        prediction,
        game=replace(
            prediction.game, start_time_utc=prediction.game.start_time_utc - timedelta(hours=1)
        ),
    )
    assert not score_prediction(
        on_time,
        run.games[LIVE_AVAILABLE],
        close=None,
        has_matched_event=True,
        has_unmatched_candidate=False,
    ).rescheduled
