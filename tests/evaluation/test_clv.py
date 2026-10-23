"""Closing odds and CLV of design 4-4, 4-5 on synthetic closing rows."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from presagio.evaluation.clv import (
    CLOSING_TOLERANCE,
    closing_market,
    closing_quotes,
    clv_for_side,
    game_clv,
    value_side,
)
from presagio.evaluation.market import Quote, prediction_market

AS_OF = datetime(2026, 10, 23, 7, 0, tzinfo=UTC)
TIP = datetime(2026, 10, 23, 23, 30, tzinfo=UTC)
_ids = iter(range(1, 10_000))


def q(
    book: str,
    side: str,
    price: str,
    at: datetime,
    market: str = "h2h",
    point: str | None = None,
    closing: bool = False,
) -> Quote:
    """One synthetic quote; closing rows carry the view's lead_time."""
    return Quote(
        odds_snapshot_id=next(_ids),
        odds_event_id=1,
        bookmaker_key=book,
        market_key=market,
        outcome_side=side,
        price_decimal=Decimal(price),
        point=None if point is None else Decimal(point),
        captured_at=at,
        lead_time=TIP - at if closing else None,
    )


PRED_AT = AS_OF - timedelta(hours=5)
CLOSE_AT = TIP - timedelta(minutes=45)


def test_closing_tolerance_keeps_quotes_within_two_hours() -> None:
    """lead 45 min and exactly 2 h count; 2 h 1 min does not (design 4-4, M-9)."""
    rows = [
        q("draftkings", "home", "1.80", TIP - timedelta(minutes=45), closing=True),
        q("fanduel", "home", "1.80", TIP - CLOSING_TOLERANCE, closing=True),
        q("betmgm", "home", "1.80", TIP - timedelta(hours=2, minutes=1), closing=True),
    ]
    kept = closing_quotes(rows)
    assert sorted(k[0] for k in kept) == ["draftkings", "fanduel"]
    assert sorted(k[0] for k in closing_quotes(rows, timedelta(hours=3))) == [
        "betmgm",
        "draftkings",
        "fanduel",
    ]


def test_closing_market_uses_consensus_and_records_lead_time() -> None:
    """Three books inside the tolerance -> consensus; lead_time = the largest one used."""
    rows = []
    for book, lead in (("draftkings", 45), ("fanduel", 30), ("betmgm", 90)):
        at = TIP - timedelta(minutes=lead)
        rows += [
            q(book, "home", "1.80", at, closing=True),
            q(book, "away", "2.10", at, closing=True),
        ]
    close = closing_market(rows)
    assert close.home is not None and close.home.source == "consensus"
    assert close.home.lead_time == timedelta(minutes=90)
    assert close.home.p_home == pytest.approx((1 / 1.8) / (1 / 1.8 + 1 / 2.1))


def test_h2h_clv_by_hand() -> None:
    """Pred DK 2.10 / 1.80 (fair home 0.461538), close DK 1.90 / 2.00 (fair home 0.512821).

    Home side: clv_prob = 0.512821 - 0.461538 = 0.051282, clv_return = 2.10 * 0.512821 - 1 =
    0.076923, beat_close = 2.10 > 1.90.
    """
    pred = prediction_market(
        [q("draftkings", "home", "2.10", PRED_AT), q("draftkings", "away", "1.80", PRED_AT)], AS_OF
    )
    close = closing_market(
        [
            q("draftkings", "home", "1.90", CLOSE_AT, closing=True),
            q("draftkings", "away", "2.00", CLOSE_AT, closing=True),
        ]
    )
    clv = game_clv("h2h", 0.55, pred, close)
    assert clv is not None and clv.side == "home" and clv.bookmaker_key == "draftkings"
    assert clv.p_fair_pred == pytest.approx(0.4615384615)
    assert clv.q_close == pytest.approx(0.5128205128)
    assert clv.clv_prob == pytest.approx(0.0512820513)
    assert clv.clv_return == pytest.approx(0.0769230769)
    assert clv.beat_close is True
    assert clv.line_move is None and clv.closing_lead_time == timedelta(minutes=45)
    away = game_clv("h2h", 0.40, pred, close)
    assert away is not None and away.side == "away"
    assert away.clv_return == pytest.approx(1.80 * (1 - 0.5128205128) - 1)
    assert away.beat_close is False


def test_clv_needs_the_same_bookmakers_close() -> None:
    """Pred reference draftkings, close only at fanduel -> CLV fields stay None."""
    pred = prediction_market(
        [q("draftkings", "home", "2.10", PRED_AT), q("draftkings", "away", "1.80", PRED_AT)], AS_OF
    )
    close = closing_market(
        [
            q("fanduel", "home", "1.90", CLOSE_AT, closing=True),
            q("fanduel", "away", "2.00", CLOSE_AT, closing=True),
        ]
    )
    clv = game_clv("h2h", 0.6, pred, close)
    assert clv is not None and clv.price_close is None and clv.clv_return is None
    with pytest.raises(ValueError, match="same bookmaker"):
        clv_for_side(pred.h2h_books["draftkings"], close.h2h_books["fanduel"], "home")


def test_totals_clv_same_line_and_moved_line() -> None:
    """Same line 220.5: CLV computed, line_move 0. Line 220.5 -> 222.5: line_move +2 and
    clv_return NULL (design 4-5)."""
    pred = prediction_market(
        [
            q("draftkings", "over", "1.95", PRED_AT, "totals", "220.5"),
            q("draftkings", "under", "1.87", PRED_AT, "totals", "220.5"),
        ],
        AS_OF,
    )
    same = closing_market(
        [
            q("draftkings", "over", "1.80", CLOSE_AT, "totals", "220.5", closing=True),
            q("draftkings", "under", "2.02", CLOSE_AT, "totals", "220.5", closing=True),
        ]
    )
    clv = game_clv("totals", 0.6, pred, same)
    assert clv is not None and clv.side == "over"
    assert clv.line_move == Decimal("0.0")
    q_close = (1 / 1.80) / (1 / 1.80 + 1 / 2.02)
    assert clv.clv_return == pytest.approx(1.95 * q_close - 1)
    assert clv.beat_close is True
    moved = closing_market(
        [
            q("draftkings", "over", "1.91", CLOSE_AT, "totals", "222.5", closing=True),
            q("draftkings", "under", "1.91", CLOSE_AT, "totals", "222.5", closing=True),
        ]
    )
    clv = game_clv("totals", 0.6, pred, moved)
    assert clv is not None and clv.line_move == Decimal("2.0")
    assert clv.clv_return is None and clv.clv_prob is None and clv.beat_close is None
    assert clv.line_close == Decimal("222.5") and clv.price_close == Decimal("1.91")


def test_value_side_and_missing_inputs() -> None:
    """Model above fair -> first side, below -> second, equal -> none; no model -> no CLV."""
    assert value_side("h2h", 0.6, 0.5) == "home"
    assert value_side("h2h", 0.4, 0.5) == "away"
    assert value_side("totals", 0.5, 0.5) is None
    pred = prediction_market(
        [q("draftkings", "home", "2.0", PRED_AT), q("draftkings", "away", "2.0", PRED_AT)], AS_OF
    )
    assert game_clv("h2h", 0.5, pred, None) is None  # equal: no side
    assert game_clv("totals", 0.6, pred, None) is None  # no totals market
    assert game_clv("h2h", None, pred, None) is None
    no_close = game_clv("h2h", 0.7, pred, None)
    assert no_close is not None and no_close.q_close is None
    with pytest.raises(ValueError, match="not a side"):
        clv_for_side(pred.h2h_books["draftkings"], None, "over")
