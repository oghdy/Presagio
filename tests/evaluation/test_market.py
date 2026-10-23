"""Market probabilities of design 4-2, 4-3, 4-6 on synthetic snapshots."""

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from presagio.evaluation.market import (
    AVAILABLE,
    CONSENSUS,
    MARKET_STATUSES,
    NO_ODDS_SEASON,
    NO_SNAPSHOT_AT_AS_OF,
    PRED_ONLY,
    RESCHEDULED,
    UNMATCHED_EVENT,
    Quote,
    book_prices,
    implied_probabilities,
    latest_quotes,
    market_status,
    power_k,
    prediction_market,
    remove_margin,
    remove_margin_power,
    remove_margin_shin,
    shin_z,
)
from presagio.ingestion.odds_math import remove_margin_proportional

AS_OF = datetime(2026, 10, 23, 7, 0, tzinfo=UTC)
_ids = iter(range(1, 10_000))


def quote(
    book: str,
    side: str,
    price: str,
    at: datetime = AS_OF - timedelta(hours=5),
    market: str = "h2h",
    point: str | None = None,
) -> Quote:
    """A synthetic odds_snapshots row."""
    return Quote(
        odds_snapshot_id=next(_ids),
        odds_event_id=1,
        bookmaker_key=book,
        market_key=market,
        outcome_side=side,
        price_decimal=Decimal(price),
        point=None if point is None else Decimal(point),
        captured_at=at,
    )


def h2h(book: str, home: str, away: str, at: datetime = AS_OF - timedelta(hours=5)) -> list:
    """Both h2h sides of one bookmaker at one capture."""
    return [quote(book, "home", home, at), quote(book, "away", away, at)]


def totals(book: str, line: str, over: str, under: str, at: datetime = AS_OF - timedelta(hours=5)):
    """Both totals sides of one bookmaker at one capture."""
    return [
        quote(book, "over", over, at, "totals", line),
        quote(book, "under", under, at, "totals", line),
    ]


FAV_LONG = [Decimal("1.4"), Decimal("3.0")]  # implied 0.714286 + 0.333333 = 1.047619


def test_implied_probabilities_are_inverse_prices() -> None:
    """1/1.4 = 0.714286, 1/3 = 0.333333 (margin 4.76%)."""
    implied = implied_probabilities(FAV_LONG)
    assert implied == pytest.approx([1 / 1.4, 1 / 3])
    assert sum(implied) - 1 == pytest.approx(0.0476190476)


def test_proportional_reuses_odds_math() -> None:
    """0.714286 / 1.047619 = 0.681818, 0.333333 / 1.047619 = 0.318182."""
    assert remove_margin(FAV_LONG, "proportional") == remove_margin_proportional(FAV_LONG)
    assert remove_margin(FAV_LONG, "proportional") == pytest.approx([0.6818181818, 0.3181818182])


def test_shin_two_outcomes_by_hand() -> None:
    """With two outcomes Shin removes the margin equally: p_i = pi_i - (S - 1) / 2.

    p_home = 0.714286 - 0.023810 = 0.690476, p_away = 0.333333 - 0.023810 = 0.309524, and the
    Shin identity pi_i^2 = S (z p_i + (1 - z) p_i^2) holds with z = 0.047986.
    """
    probs = remove_margin_shin(FAV_LONG)
    z = shin_z(FAV_LONG)
    implied = implied_probabilities(FAV_LONG)
    total = sum(implied)
    assert sum(probs) == pytest.approx(1.0, abs=1e-12)
    assert probs == pytest.approx([pi - (total - 1) / 2 for pi in implied], abs=1e-12)
    assert probs == pytest.approx([0.6904761905, 0.3095238095], abs=1e-9)
    assert z == pytest.approx(0.0479864963, abs=1e-9)
    for pi, p in zip(implied, probs, strict=True):
        assert pi == pytest.approx(math.sqrt(total * (z * p + (1 - z) * p * p)), abs=1e-12)


def test_power_two_outcomes_by_hand() -> None:
    """k = 1.081059: 0.714286^k = 0.695067, 0.333333^k = 0.304933, sum 1."""
    probs = remove_margin_power(FAV_LONG)
    k = power_k(FAV_LONG)
    assert sum(probs) == pytest.approx(1.0, abs=1e-12)
    assert k == pytest.approx(1.081059044, abs=1e-8)
    assert probs == pytest.approx([0.6950674732, 0.3049325268], abs=1e-9)
    assert [pi**k for pi in implied_probabilities(FAV_LONG)] == pytest.approx(probs)


@pytest.mark.parametrize(
    "prices",
    [("1.4", "3.0"), ("1.25", "4.2"), ("1.05", "11.0"), ("2.10", "1.80"), ("1.9091", "1.9091")],
)
def test_shin_and_power_sum_to_one_and_cut_the_longshot_more(prices: tuple[str, str]) -> None:
    """Both methods give sum 1 and leave the longshot below its proportional probability."""
    decimals = [Decimal(p) for p in prices]
    proportional = remove_margin_proportional(decimals)
    longshot = 0 if decimals[0] > decimals[1] else 1
    for method in ("shin", "power"):
        probs = remove_margin(decimals, method)
        assert sum(probs) == pytest.approx(1.0, abs=1e-12)
        if decimals[0] == decimals[1]:
            assert probs == pytest.approx([0.5, 0.5])
        else:
            assert probs[longshot] < proportional[longshot]
            assert probs[1 - longshot] > proportional[1 - longshot]


def test_margin_removal_rejects_negative_margin_and_unknown_methods() -> None:
    """Implied sum below 1 is not a bookmaker market; unknown methods are errors."""
    with pytest.raises(ValueError, match="negative margin"):
        remove_margin_shin([Decimal("2.2"), Decimal("2.2")])
    with pytest.raises(ValueError, match="unknown margin method"):
        remove_margin(FAV_LONG, "logit")
    assert remove_margin_power([Decimal("2.0"), Decimal("2.0")]) == [0.5, 0.5]


def test_r3_picks_last_quote_at_or_before_as_of_within_24_hours() -> None:
    """T6 shape: as_of - 1 min (1.90/2.00), = as_of (1.95/1.95), as_of + 1 min (2.50/1.60).

    The as_of capture wins (equality included), the later one is ignored, and a quote older
    than 24 hours is no market.
    """
    quotes = [
        *h2h("draftkings", "1.90", "2.00", AS_OF - timedelta(minutes=1)),
        *h2h("draftkings", "1.95", "1.95", AS_OF),
        *h2h("draftkings", "2.50", "1.60", AS_OF + timedelta(minutes=1)),
    ]
    market = prediction_market(quotes, AS_OF)
    assert market.home is not None
    assert market.home.p_home == pytest.approx(0.5)
    assert market.home.source == "draftkings" and market.home.captured_at == AS_OF
    stale = h2h("draftkings", "1.95", "1.95", AS_OF - timedelta(hours=24, seconds=1))
    assert prediction_market(stale, AS_OF).home is None
    edge = h2h("draftkings", "1.95", "1.95", AS_OF - timedelta(hours=24))
    assert prediction_market(edge, AS_OF).home is not None
    assert latest_quotes(quotes, AS_OF - timedelta(minutes=2)) == {}


def test_consensus_median_needs_three_books_with_both_sides_at_one_capture() -> None:
    """Three books -> median of their fair home probabilities; a book whose latest home and
    away quotes are from different captures does not count."""
    quotes = [
        *h2h("draftkings", "1.50", "2.70"),  # p_home 0.642857
        *h2h("fanduel", "1.60", "2.45"),  # 0.604938
        *h2h("betmgm", "1.55", "2.55"),  # 0.621951
        quote("bovada", "home", "1.10", AS_OF - timedelta(hours=2)),
        quote("bovada", "away", "8.00", AS_OF - timedelta(hours=3)),
    ]
    market = prediction_market(quotes, AS_OF)
    assert market.home is not None
    assert market.home.source == CONSENSUS
    assert market.home.bookmakers == ("betmgm", "draftkings", "fanduel")
    assert market.home.p_home == pytest.approx((1 / 1.55) / (1 / 1.55 + 1 / 2.55))
    assert len(market.home.snapshot_ids) == 6
    assert "bovada" not in market.h2h_books


def test_fewer_than_three_books_fall_back_to_the_reference_order() -> None:
    """Two books: no consensus; draftkings missing -> fanduel; neither -> betmgm; none -> None."""
    two = [*h2h("fanduel", "1.60", "2.45"), *h2h("betmgm", "1.55", "2.55")]
    market = prediction_market(two, AS_OF)
    assert market.home is not None and market.home.source == "fanduel"
    assert market.home.p_home == pytest.approx((1 / 1.6) / (1 / 1.6 + 1 / 2.45))
    only_mgm = prediction_market(h2h("betmgm", "1.55", "2.55"), AS_OF)
    assert only_mgm.home is not None and only_mgm.home.source == "betmgm"
    other = prediction_market(h2h("bovada", "1.55", "2.55"), AS_OF)
    assert other.home is None and other.h2h_reference is None
    with_dk = prediction_market([*two, *h2h("draftkings", "1.50", "2.70")], AS_OF)
    assert with_dk.h2h_reference is not None
    assert with_dk.h2h_reference.bookmaker_key == "draftkings"


def test_margin_method_applies_to_consensus() -> None:
    """Shin consensus differs from proportional and still sums to 1 per book."""
    quotes = [*h2h("draftkings", "1.4", "3.0")]
    shin = prediction_market(quotes, AS_OF, method="shin")
    assert shin.home is not None and shin.home.method == "shin"
    assert shin.home.p_home == pytest.approx(0.6904761905, abs=1e-9)


def test_total_line_is_the_reference_bookmakers_last_line() -> None:
    """L = draftkings' totals point at the last capture <= as_of; mismatched sides skip."""
    quotes = [
        *totals("fanduel", "221.5", "1.91", "1.91"),
        *totals("draftkings", "220.5", "1.95", "1.87", AS_OF - timedelta(hours=8)),
        *totals("draftkings", "222.5", "1.91", "1.91", AS_OF - timedelta(hours=1)),
        *totals("draftkings", "230.5", "1.91", "1.91", AS_OF + timedelta(minutes=5)),
    ]
    market = prediction_market(quotes, AS_OF)
    assert market.total_line == 222.5
    assert market.totals_reference is not None
    assert market.totals_reference.bookmaker_key == "draftkings"
    broken = [
        quote("draftkings", "over", "1.91", market="totals", point="222.5"),
        quote("draftkings", "under", "1.91", market="totals", point="223.5"),
        *totals("fanduel", "221.5", "1.91", "1.91"),
    ]
    fallback = prediction_market(broken, AS_OF)
    assert fallback.total_line == 221.5
    assert fallback.totals_reference is not None
    assert fallback.totals_reference.bookmaker_key == "fanduel"
    assert prediction_market(h2h("draftkings", "1.9", "1.9"), AS_OF).total_line is None


def test_spreads_pair_opposite_points() -> None:
    """Spreads keep a book only when home and away points are opposite."""
    good = [
        quote("draftkings", "home", "1.91", market="spreads", point="-3.5"),
        quote("draftkings", "away", "1.91", market="spreads", point="3.5"),
    ]
    bad = [
        quote("fanduel", "home", "1.91", market="spreads", point="-3.5"),
        quote("fanduel", "away", "1.91", market="spreads", point="4.5"),
    ]
    books = book_prices(latest_quotes([*good, *bad], AS_OF), "spreads")
    assert list(books) == ["draftkings"] and books["draftkings"].point == Decimal("-3.5")


def test_market_reference_is_json_ready() -> None:
    """market_reference holds method, source, books, capture time and snapshot ids."""
    ref = prediction_market(h2h("draftkings", "1.95", "1.95", AS_OF), AS_OF).reference()
    assert ref["method"] == "proportional" and ref["source"] == "draftkings"
    assert ref["n_books"] == 1 and ref["captured_at"] == AS_OF.isoformat()
    assert len(ref["snapshot_ids"]) == 2 and ref["total_line"] is None


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        (
            {"rescheduled": True, "has_prediction_market": True, "has_closing_market": True},
            RESCHEDULED,
        ),
        ({"has_prediction_market": True, "has_closing_market": True}, AVAILABLE),
        ({"has_prediction_market": True}, PRED_ONLY),
        ({"season": "2024-25"}, NO_ODDS_SEASON),
        ({"season": "2024-25", "has_prediction_market": True}, PRED_ONLY),  # after DR-7
        ({"has_unmatched_candidate": True}, UNMATCHED_EVENT),
        ({"has_unmatched_candidate": True, "has_matched_event": True}, NO_SNAPSHOT_AT_AS_OF),
        ({"has_matched_event": True}, NO_SNAPSHOT_AT_AS_OF),
        ({}, NO_SNAPSHOT_AT_AS_OF),
    ],
)
def test_market_status_branches(kwargs: dict, expected: str) -> None:
    """Every one of the six values of design 4-6 is reachable, in the documented order."""
    args = {
        "season": "2026-27",
        "rescheduled": False,
        "has_prediction_market": False,
        "has_closing_market": False,
        "has_matched_event": False,
        "has_unmatched_candidate": False,
        **kwargs,
    }
    assert market_status(**args) == expected
    assert expected in MARKET_STATUSES
