"""Market probabilities (modeling design v1.2 4-2, 4-3, 4-6): margin removal, consensus,
reference bookmaker, prediction-time odds (rule R3) and market_status.

Pure functions over Quote objects; presagio.evaluation.data loads the quotes. Prediction-time
odds never come from the view odds_closing_lines (closing odds are evaluation-only, 4-4;
presagio.evaluation.clv reads that view).

Margin removal for one market with decimal prices o_i and implied probabilities
pi_i = 1 / o_i (S = sum pi_i, overround S - 1):

- proportional (D-036, the default):  p_i = pi_i / S   (presagio.ingestion.odds_math)
- Shin: p_i = (sqrt(z^2 + 4 (1 - z) pi_i^2 / S) - z) / (2 (1 - z)), z in [0, 1) solved
  numerically so that sum p_i = 1 (design 4-2; Strumbelj 2014, design 12-2).
- power: p_i = pi_i^k, k >= 1 solved numerically so that sum p_i = 1 (design 4-2; Clarke,
  Kovalchik & Ingram 2017, design 12-2).

Shin and power take more off the longshot than proportional does (favourite-longshot bias).
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from presagio.ingestion.odds_math import implied_probability, remove_margin_proportional

MARKET_SIDES: dict[str, tuple[str, str]] = {
    "h2h": ("home", "away"),
    "spreads": ("home", "away"),
    "totals": ("over", "under"),
}
# Design 4-3 / D-049 M-4: reference bookmaker (lines, CLV, recommendation prices), in order.
REFERENCE_BOOKMAKERS = ("draftkings", "fanduel", "betmgm")
# Design 4-3: consensus only with at least this many bookmakers with both sides (proposal).
CONSENSUS_MIN_BOOKS = 3
# Design 4-3 rule R3: a prediction-time quote older than this is "no market" (proposal).
MAX_MARKET_AGE = timedelta(hours=24)
MARGIN_METHODS = ("proportional", "shin", "power")
# D-036 / D-049 M-5: proportional until H-02 decides otherwise.
DEFAULT_MARGIN_METHOD = "proportional"
CONSENSUS = "consensus"

# Design 4-6 / D-049 M-8: the six market_status values.
AVAILABLE = "available"
PRED_ONLY = "pred_only"
NO_SNAPSHOT_AT_AS_OF = "no_snapshot_at_as_of"
NO_ODDS_SEASON = "no_odds_season"
UNMATCHED_EVENT = "unmatched_event"
RESCHEDULED = "rescheduled"
MARKET_STATUSES = (
    AVAILABLE,
    PRED_ONLY,
    NO_SNAPSHOT_AT_AS_OF,
    NO_ODDS_SEASON,
    UNMATCHED_EVENT,
    RESCHEDULED,
)
# Comparison sets (design 4-6): prediction-time market (B3) vs closing market and CLV (B4).
B3_STATUSES = frozenset({AVAILABLE, PRED_ONLY})
B4_STATUSES = frozenset({AVAILABLE})
# Seasons without odds before the paid history backfill (D-026, DR-7). A game of these seasons
# that has a prediction-time market anyway (after DR-7) is classified by its data, not by this.
NO_ODDS_SEASONS = ("2023-24", "2024-25", "2025-26")

_SOLVER_ITERATIONS = 200


def implied_probabilities(prices: Sequence[Decimal | float | str]) -> list[float]:
    """1 / o_i for each decimal price (margin included, D-036)."""
    if len(prices) < 2:
        raise ValueError("a market needs at least two outcomes")
    return [implied_probability(p) for p in prices]


def _implied_with_margin(prices: Sequence[Decimal | float | str]) -> tuple[list[float], float]:
    """Implied probabilities and their sum S; ValueError for a negative margin (S < 1)."""
    implied = implied_probabilities(prices)
    total = sum(implied)
    if total < 1.0:
        raise ValueError(f"implied probabilities sum to {total:.6f} < 1 (negative margin)")
    return implied, total


def _bisect(f: Any, lo: float, hi: float) -> float:
    """Root of a function that changes sign from positive at lo to negative at hi."""
    for _ in range(_SOLVER_ITERATIONS):
        mid = (lo + hi) / 2.0
        if f(mid) > 0:
            lo = mid
        else:
            hi = mid
        if hi - lo <= 1e-16:
            break
    return (lo + hi) / 2.0


def shin_probabilities(implied: Sequence[float], z: float) -> list[float]:
    """Shin's p_i(z) for implied probabilities pi_i (S = their sum)."""
    total = sum(implied)
    if z == 0.0:
        return [pi / math.sqrt(total) for pi in implied]
    return [
        (math.sqrt(z * z + 4.0 * (1.0 - z) * pi * pi / total) - z) / (2.0 * (1.0 - z))
        for pi in implied
    ]


def shin_z(prices: Sequence[Decimal | float | str]) -> float:
    """Shin's insider share z in [0, 1) that makes the probabilities sum to 1."""
    implied, total = _implied_with_margin(prices)
    if total == 1.0:
        return 0.0
    return _bisect(lambda z: sum(shin_probabilities(implied, z)) - 1.0, 0.0, 1.0 - 1e-12)


def remove_margin_shin(prices: Sequence[Decimal | float | str]) -> list[float]:
    """Fair probabilities by Shin's method (sum 1 up to solver precision)."""
    implied, _ = _implied_with_margin(prices)
    return shin_probabilities(implied, shin_z(prices))


def power_k(prices: Sequence[Decimal | float | str]) -> float:
    """Exponent k >= 1 with sum pi_i^k = 1."""
    implied, total = _implied_with_margin(prices)
    if total == 1.0:
        return 1.0

    def excess(k: float) -> float:
        """sum pi_i^k - 1 (decreasing in k)."""
        return sum(pi**k for pi in implied) - 1.0

    hi = 2.0
    while excess(hi) > 0:
        hi *= 2.0
    return _bisect(excess, 1.0, hi)


def remove_margin_power(prices: Sequence[Decimal | float | str]) -> list[float]:
    """Fair probabilities by the power method (sum 1 up to solver precision)."""
    implied, _ = _implied_with_margin(prices)
    k = power_k(prices)
    return [pi**k for pi in implied]


def remove_margin(prices: Sequence[Decimal | float | str], method: str) -> list[float]:
    """Fair probabilities by the named method (proportional, shin, power)."""
    if method == "proportional":
        return remove_margin_proportional(prices)
    if method == "shin":
        return remove_margin_shin(prices)
    if method == "power":
        return remove_margin_power(prices)
    raise ValueError(f"unknown margin method {method!r}; expected one of {MARGIN_METHODS}")


@dataclass(frozen=True)
class Quote:
    """One odds_snapshots row (or odds_closing_lines row) as the evaluation sees it."""

    odds_snapshot_id: int
    odds_event_id: int
    bookmaker_key: str
    market_key: str
    outcome_side: str
    price_decimal: Decimal
    point: Decimal | None
    captured_at: datetime
    lead_time: timedelta | None = None  # closing rows only (view column)

    @property
    def key(self) -> tuple[str, str, str]:
        """(bookmaker, market, side): one quote per key per game is kept."""
        return (self.bookmaker_key, self.market_key, self.outcome_side)


def latest_quotes(
    quotes: Iterable[Quote], as_of: datetime, max_age: timedelta = MAX_MARKET_AGE
) -> dict[tuple[str, str, str], Quote]:
    """Rule R3: per (bookmaker, market, side) the last quote with captured_at <= as_of,
    kept only when as_of - captured_at <= max_age. captured_at (our receive time) is the
    clock; bookmaker_last_update is never used (design 4-3)."""
    latest: dict[tuple[str, str, str], Quote] = {}
    for quote in quotes:
        if quote.captured_at > as_of:
            continue
        held = latest.get(quote.key)
        if held is None or (quote.captured_at, quote.odds_snapshot_id) > (
            held.captured_at,
            held.odds_snapshot_id,
        ):
            latest[quote.key] = quote
    return {k: q for k, q in latest.items() if as_of - q.captured_at <= max_age}


@dataclass(frozen=True)
class BookPrice:
    """One bookmaker's two-sided market from quotes captured at the same moment."""

    bookmaker_key: str
    market_key: str
    captured_at: datetime
    prices: Mapping[str, Decimal]  # side -> decimal price
    fair: Mapping[str, float]  # side -> margin-free probability
    point: Decimal | None  # totals: the line L; spreads: the home point; h2h: None
    snapshot_ids: tuple[int, ...]
    method: str
    lead_time: timedelta | None = None  # closing books: the larger lead time of the two sides


def book_prices(
    quotes: Mapping[tuple[str, str, str], Quote],
    market_key: str,
    method: str = DEFAULT_MARGIN_METHOD,
) -> dict[str, BookPrice]:
    """Bookmakers whose kept quotes for market_key have both sides at the same captured_at
    (and a consistent line: equal totals points, opposite spread points)."""
    first, second = MARKET_SIDES[market_key]
    books = sorted({b for b, m, _ in quotes if m == market_key})
    result: dict[str, BookPrice] = {}
    for book in books:
        a = quotes.get((book, market_key, first))
        b = quotes.get((book, market_key, second))
        if a is None or b is None or a.captured_at != b.captured_at:
            continue
        if market_key == "totals" and a.point != b.point:
            continue
        if market_key == "spreads" and (a.point is None or b.point is None or a.point != -b.point):
            continue
        fair = remove_margin([a.price_decimal, b.price_decimal], method)
        leads = [q.lead_time for q in (a, b) if q.lead_time is not None]
        result[book] = BookPrice(
            bookmaker_key=book,
            market_key=market_key,
            captured_at=a.captured_at,
            prices={first: a.price_decimal, second: b.price_decimal},
            fair={first: fair[0], second: fair[1]},
            point=a.point,
            snapshot_ids=(a.odds_snapshot_id, b.odds_snapshot_id),
            method=method,
            lead_time=max(leads) if leads else None,
        )
    return result


def reference_book(
    books: Mapping[str, BookPrice], order: Sequence[str] = REFERENCE_BOOKMAKERS
) -> BookPrice | None:
    """The first bookmaker of the fixed fallback order that has the market (4-3)."""
    for key in order:
        if key in books:
            return books[key]
    return None


@dataclass(frozen=True)
class MarketProbability:
    """Margin-free home win probability and where it came from."""

    p_home: float
    source: str  # "consensus" or the reference bookmaker key
    bookmakers: tuple[str, ...]
    captured_at: datetime  # newest capture used
    oldest_captured_at: datetime
    snapshot_ids: tuple[int, ...]
    method: str
    lead_time: timedelta | None = None  # closing: the largest lead time used

    @property
    def n_books(self) -> int:
        """Number of bookmakers behind the value."""
        return len(self.bookmakers)


def home_probability(
    books: Mapping[str, BookPrice],
    min_books: int = CONSENSUS_MIN_BOOKS,
    order: Sequence[str] = REFERENCE_BOOKMAKERS,
) -> MarketProbability | None:
    """Design 4-3: median over >= min_books two-sided h2h books, else the reference
    bookmaker's value, else None."""
    h2h = [b for b in books.values() if b.market_key == "h2h"]
    if len(h2h) >= min_books:
        used = sorted(h2h, key=lambda b: b.bookmaker_key)
        p_home = statistics.median(b.fair["home"] for b in used)
        source = CONSENSUS
    else:
        ref = reference_book({b.bookmaker_key: b for b in h2h}, order)
        if ref is None:
            return None
        used, p_home, source = [ref], ref.fair["home"], ref.bookmaker_key
    leads = [b.lead_time for b in used if b.lead_time is not None]
    return MarketProbability(
        p_home=p_home,
        source=source,
        bookmakers=tuple(b.bookmaker_key for b in used),
        captured_at=max(b.captured_at for b in used),
        oldest_captured_at=min(b.captured_at for b in used),
        snapshot_ids=tuple(i for b in used for i in b.snapshot_ids),
        method=used[0].method,
        lead_time=max(leads) if leads else None,
    )


@dataclass(frozen=True)
class GameMarket:
    """One game's market at one moment: the prediction-time market (R3) or the close."""

    h2h_books: Mapping[str, BookPrice] = field(default_factory=dict)
    totals_books: Mapping[str, BookPrice] = field(default_factory=dict)
    spreads_books: Mapping[str, BookPrice] = field(default_factory=dict)
    home: MarketProbability | None = None  # B3 at prediction time, B4 at the close
    h2h_reference: BookPrice | None = None
    totals_reference: BookPrice | None = None  # its point is the reference line L
    spreads_reference: BookPrice | None = None

    def books(self, market_key: str) -> Mapping[str, BookPrice]:
        """Two-sided books of one market."""
        return {"h2h": self.h2h_books, "totals": self.totals_books, "spreads": self.spreads_books}[
            market_key
        ]

    def reference_for(self, market_key: str) -> BookPrice | None:
        """The reference bookmaker's book of one market (fixed fallback order)."""
        return {
            "h2h": self.h2h_reference,
            "totals": self.totals_reference,
            "spreads": self.spreads_reference,
        }[market_key]

    @property
    def total_line(self) -> float | None:
        """Reference total line L (the reference bookmaker's totals point), if any."""
        ref = self.totals_reference
        return None if ref is None or ref.point is None else float(ref.point)

    def reference(self) -> dict[str, Any]:
        """JSON-ready summary for predictions.market_reference (P-033 stores it)."""
        home = self.home
        return {
            "method": home.method if home else None,
            "source": home.source if home else None,
            "n_books": home.n_books if home else 0,
            "bookmakers": list(home.bookmakers) if home else [],
            "captured_at": home.captured_at.isoformat() if home else None,
            "oldest_captured_at": home.oldest_captured_at.isoformat() if home else None,
            "snapshot_ids": list(home.snapshot_ids) if home else [],
            "h2h_reference_bookmaker": self.h2h_reference.bookmaker_key
            if self.h2h_reference
            else None,
            "totals_reference_bookmaker": self.totals_reference.bookmaker_key
            if self.totals_reference
            else None,
            "total_line": self.total_line,
        }


def game_market(
    quotes: Mapping[tuple[str, str, str], Quote],
    *,
    method: str = DEFAULT_MARGIN_METHOD,
    min_books: int = CONSENSUS_MIN_BOOKS,
    order: Sequence[str] = REFERENCE_BOOKMAKERS,
) -> GameMarket:
    """Books, consensus home probability and reference books from one game's kept quotes."""
    h2h = book_prices(quotes, "h2h", method)
    totals = book_prices(quotes, "totals", method)
    spreads = book_prices(quotes, "spreads", method)
    return GameMarket(
        h2h_books=h2h,
        totals_books=totals,
        spreads_books=spreads,
        home=home_probability(h2h, min_books, order),
        h2h_reference=reference_book(h2h, order),
        totals_reference=reference_book(totals, order),
        spreads_reference=reference_book(spreads, order),
    )


def prediction_market(
    quotes: Iterable[Quote],
    as_of: datetime,
    *,
    method: str = DEFAULT_MARGIN_METHOD,
    min_books: int = CONSENSUS_MIN_BOOKS,
    order: Sequence[str] = REFERENCE_BOOKMAKERS,
    max_age: timedelta = MAX_MARKET_AGE,
) -> GameMarket:
    """Prediction-time market of one game: rule R3 (captured_at <= as_of, within max_age)."""
    kept = latest_quotes(quotes, as_of, max_age)
    return game_market(kept, method=method, min_books=min_books, order=order)


def market_status(
    *,
    season: str,
    rescheduled: bool,
    has_prediction_market: bool,
    has_closing_market: bool,
    has_matched_event: bool,
    has_unmatched_candidate: bool,
    no_odds_seasons: Sequence[str] = NO_ODDS_SEASONS,
) -> str:
    """Design 4-6: why a game can or cannot be compared with the market.

    Order: rescheduled games leave every market comparison (1-2); with a prediction-time
    home probability the game is `available` (close within tolerance) or `pred_only`;
    without one it is `no_odds_season` (seasons before DR-7), `unmatched_event` (no event
    mapped to the game but an unmatched candidate exists, D-036) or `no_snapshot_at_as_of`.
    """
    if rescheduled:
        return RESCHEDULED
    if has_prediction_market:
        return AVAILABLE if has_closing_market else PRED_ONLY
    if season in no_odds_seasons:
        return NO_ODDS_SEASON
    if not has_matched_event and has_unmatched_candidate:
        return UNMATCHED_EVENT
    return NO_SNAPSHOT_AT_AS_OF
