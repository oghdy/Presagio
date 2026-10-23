"""Closing odds and closing line value (modeling design v1.2 4-4, 4-5).

This module is the only reader of the view odds_closing_lines (design 2-1, 8-4, 9-2 Q4):
closing odds are known only after tip-off, so they serve evaluation and CLV, never features
or models (tests/evaluation/test_boundaries.py fails if src/presagio/features or models name
the view).

Closing = the view's row (last snapshot before the event's latest commence time, snapshots
taken under a schedule more than 1 hour off excluded, D-028 A-8) with lead_time <= 2 hours
(design 4-4, D-049 M-9; a setting). CLV for side s, at the prediction's reference bookmaker,
same market and side (and same line for totals and spreads):

    clv_prob   = q_close(s) - p_fair_pred(s)
    clv_return = o_pred * q_close(s) - 1
    beat_close = o_pred > o_close
    line_move  = L_close - L_pred      (totals/spreads; a different line leaves the CLV NULL)
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import Connection, text

from presagio.evaluation.market import (
    CONSENSUS_MIN_BOOKS,
    DEFAULT_MARGIN_METHOD,
    MARKET_SIDES,
    REFERENCE_BOOKMAKERS,
    BookPrice,
    GameMarket,
    Quote,
    game_market,
)

# Design 4-4 / D-049 M-9: a closing quote counts only this close to tip-off (proposal value).
CLOSING_TOLERANCE = timedelta(hours=2)

CLOSING_LINES_SQL = text(
    """
    SELECT game_id, odds_snapshot_id, odds_event_id, bookmaker_key, market_key, outcome_side,
           price_decimal, point, captured_at, lead_time
    FROM odds_closing_lines
    WHERE game_id = ANY(:game_ids)
    ORDER BY game_id, odds_event_id, bookmaker_key, market_key, outcome_side
    """
)


def load_closing_quotes(conn: Connection, game_ids: Sequence[str]) -> dict[str, list[Quote]]:
    """Closing rows of the view per game (every lead time; tolerance is applied later)."""
    by_game: dict[str, list[Quote]] = {}
    if not game_ids:
        return by_game
    for row in conn.execute(CLOSING_LINES_SQL, {"game_ids": list(game_ids)}).mappings():
        by_game.setdefault(row["game_id"], []).append(
            Quote(
                odds_snapshot_id=row["odds_snapshot_id"],
                odds_event_id=row["odds_event_id"],
                bookmaker_key=row["bookmaker_key"],
                market_key=row["market_key"],
                outcome_side=row["outcome_side"],
                price_decimal=row["price_decimal"],
                point=row["point"],
                captured_at=row["captured_at"],
                lead_time=row["lead_time"],
            )
        )
    return by_game


def closing_quotes(
    quotes: Iterable[Quote], tolerance: timedelta = CLOSING_TOLERANCE
) -> dict[tuple[str, str, str], Quote]:
    """Per (bookmaker, market, side) the latest closing quote with lead_time <= tolerance
    (latest across events when more than one event maps to the game)."""
    kept: dict[tuple[str, str, str], Quote] = {}
    for quote in quotes:
        if quote.lead_time is None or quote.lead_time > tolerance:
            continue
        held = kept.get(quote.key)
        if held is None or (quote.captured_at, quote.odds_snapshot_id) > (
            held.captured_at,
            held.odds_snapshot_id,
        ):
            kept[quote.key] = quote
    return kept


def closing_market(
    quotes: Iterable[Quote],
    *,
    tolerance: timedelta = CLOSING_TOLERANCE,
    method: str = DEFAULT_MARGIN_METHOD,
    min_books: int = CONSENSUS_MIN_BOOKS,
    order: Sequence[str] = REFERENCE_BOOKMAKERS,
) -> GameMarket:
    """The closing market of one game (B4): same consensus/reference rules as 4-3."""
    return game_market(
        closing_quotes(quotes, tolerance), method=method, min_books=min_books, order=order
    )


@dataclass(frozen=True)
class Clv:
    """Closing line value of one side at one bookmaker (design 4-5)."""

    market_key: str
    side: str
    bookmaker_key: str
    price_pred: Decimal
    p_fair_pred: float
    line_pred: Decimal | None
    price_close: Decimal | None = None
    q_close: float | None = None
    line_close: Decimal | None = None
    line_move: Decimal | None = None
    clv_prob: float | None = None
    clv_return: float | None = None
    beat_close: bool | None = None
    closing_lead_time: timedelta | None = None


def clv_for_side(pred: BookPrice, close: BookPrice | None, side: str) -> Clv:
    """CLV of `side` priced by `pred`, against the same bookmaker's close (or none)."""
    if side not in MARKET_SIDES[pred.market_key]:
        raise ValueError(f"side {side!r} is not a side of {pred.market_key}")
    if close is not None and (
        close.bookmaker_key != pred.bookmaker_key or close.market_key != pred.market_key
    ):
        raise ValueError("CLV compares the same bookmaker and market only")
    base = Clv(
        market_key=pred.market_key,
        side=side,
        bookmaker_key=pred.bookmaker_key,
        price_pred=pred.prices[side],
        p_fair_pred=pred.fair[side],
        line_pred=pred.point,
    )
    if close is None:
        return base
    line_move = None
    if pred.point is not None and close.point is not None:
        line_move = close.point - pred.point
    common = {
        "price_close": close.prices[side],
        "q_close": close.fair[side],
        "line_close": close.point,
        "line_move": line_move,
        "closing_lead_time": close.lead_time,
    }
    if pred.point != close.point:  # the line moved: only line_move is meaningful
        return Clv(**{**base.__dict__, **common})
    q_close = close.fair[side]
    return Clv(
        **{
            **base.__dict__,
            **common,
            "clv_prob": q_close - pred.fair[side],
            "clv_return": float(pred.prices[side]) * q_close - 1.0,
            "beat_close": pred.prices[side] > close.prices[side],
        }
    )


def value_side(market_key: str, p_model_first: float, p_fair_first: float) -> str | None:
    """The side the model values (design 4-5): first side (home/over) when the model's
    probability of it exceeds the fair prediction-time probability, the other side when it
    is lower, None when equal."""
    first, second = MARKET_SIDES[market_key]
    if p_model_first > p_fair_first:
        return first
    if p_model_first < p_fair_first:
        return second
    return None


def game_clv(
    market_key: str,
    p_model_first: float | None,
    pred: GameMarket,
    close: GameMarket | None,
) -> Clv | None:
    """CLV of the model's value side at the prediction-time reference bookmaker."""
    ref = pred.reference_for(market_key)
    if ref is None or p_model_first is None:
        return None
    first = MARKET_SIDES[market_key][0]
    side = value_side(market_key, p_model_first, ref.fair[first])
    if side is None:
        return None
    close_book = None if close is None else close.books(market_key).get(ref.bookmaker_key)
    return clv_for_side(ref, close_book, side)
