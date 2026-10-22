"""Odds arithmetic, checked against values computed by hand."""

from decimal import Decimal

import pytest

from presagio.ingestion.odds_math import (
    american_to_decimal,
    decimal_to_american,
    implied_probability,
    overround,
    remove_margin_proportional,
    to_decimal_price,
)


@pytest.mark.parametrize(
    ("american", "expected"),
    [
        (150, "2.5000"),  # 1 + 150/100
        (-200, "1.5000"),  # 1 + 100/200
        (-110, "1.9091"),  # 1 + 100/110 = 1.90909..., rounded half up at 4 places
        (100, "2.0000"),
        (-100, "2.0000"),
        (250, "3.5000"),
        (-150, "1.6667"),  # 1 + 100/150 = 1.66666...
        (-125, "1.8000"),
        (104, "2.0400"),
        ("+120", "2.2000"),
        (Decimal("-105"), "1.9524"),  # 1 + 100/105 = 1.952380...
        (-10000, "1.0100"),
    ],
)
def test_american_to_decimal(american: object, expected: str) -> None:
    """Positive: 1 + a/100. Negative: 1 + 100/|a|. Stored with 4 decimal places."""
    assert american_to_decimal(american) == Decimal(expected)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", [0, 50, -99, 99.5, True, "abc", float("nan"), float("inf")])
def test_american_to_decimal_rejects_impossible_values(bad: object) -> None:
    """American odds live outside (-100, +100); non-numbers are refused."""
    with pytest.raises(ValueError):
        american_to_decimal(bad)  # type: ignore[arg-type]


def test_american_to_decimal_refuses_prices_that_round_to_one() -> None:
    """-1,000,000 is 1.0001; -100,000,000 would round to 1.0000, which the DB refuses."""
    assert american_to_decimal(-1_000_000) == Decimal("1.0001")
    with pytest.raises(ValueError, match="not above 1"):
        american_to_decimal(-100_000_000)


@pytest.mark.parametrize(
    ("price", "expected"),
    [("2.5", "150.00"), ("1.5", "-200.00"), ("2.0", "100.00"), ("1.9091", "-110.00")],
)
def test_decimal_to_american(price: str, expected: str) -> None:
    """>= 2: (d-1)*100. < 2: -100/(d-1). 1.9091 -> -109.999 -> -110.00."""
    assert decimal_to_american(price) == Decimal(expected)


@pytest.mark.parametrize("american", [-500, -250, -110, -101, 100, 101, 135, 300, 500])
def test_round_trip_stays_within_rounding(american: int) -> None:
    """American -> decimal (4 places) -> American is within 0.5 of the start."""
    back = decimal_to_american(american_to_decimal(american))
    assert abs(back - american) <= Decimal("0.5")


@pytest.mark.parametrize(
    ("price", "odds_format", "expected"),
    [
        (1.91, "decimal", "1.9100"),
        ("1.90909", "decimal", "1.9091"),
        (2, "decimal", "2.0000"),
        (-110, "american", "1.9091"),
        (150, "american", "2.5000"),
    ],
)
def test_to_decimal_price_unifies_formats(price: object, odds_format: str, expected: str) -> None:
    """Both formats end up as the same stored decimal value."""
    assert to_decimal_price(price, odds_format) == Decimal(expected)  # type: ignore[arg-type]


@pytest.mark.parametrize(("price", "odds_format"), [(1.0, "decimal"), ("1.00004", "decimal")])
def test_to_decimal_price_refuses_one_or_less(price: object, odds_format: str) -> None:
    """odds_snapshots CHECK price_decimal > 1 after rounding to 4 places."""
    with pytest.raises(ValueError):
        to_decimal_price(price, odds_format)  # type: ignore[arg-type]


def test_to_decimal_price_refuses_unknown_format() -> None:
    """Only 'decimal' and 'american'."""
    with pytest.raises(ValueError, match="unknown odds format"):
        to_decimal_price(1.5, "fractional")


def test_implied_probability_and_overround() -> None:
    """1/2.0 = 0.5; two sides at 1.91: 2/1.91 - 1 = 0.047120... (4.71 % margin)."""
    assert implied_probability(2.0) == 0.5
    assert implied_probability(Decimal("1.25")) == pytest.approx(0.8)
    assert overround([1.91, 1.91]) == pytest.approx(2 / 1.91 - 1)
    assert overround([1.91, 1.91]) == pytest.approx(0.0471204, abs=1e-7)


def test_remove_margin_proportional_by_hand() -> None:
    """1.5 and 2.6: implied 2/3 and 5/13, sum 41/39, fair 26/41 and 15/41."""
    home, away = remove_margin_proportional([1.5, 2.6])
    assert home == pytest.approx(26 / 41)  # 0.634146...
    assert away == pytest.approx(15 / 41)  # 0.365853...
    assert home + away == pytest.approx(1.0)
    assert remove_margin_proportional(["1.91", "1.91"]) == pytest.approx([0.5, 0.5])


def test_remove_margin_needs_two_outcomes() -> None:
    """One price is not a market."""
    with pytest.raises(ValueError):
        remove_margin_proportional([1.5])
    with pytest.raises(ValueError):
        overround([1.5])
