"""Odds arithmetic: American <-> decimal prices, implied probabilities, margin removal.

Pure functions, no I/O. Prices are Decimal because odds_snapshots.price_decimal is
numeric(10,4); probabilities are float because they only feed calculations.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# odds_snapshots.price_decimal is numeric(10,4): four decimal places.
PRICE_QUANTUM = Decimal("0.0001")
ODDS_FORMATS = ("decimal", "american")
_HUNDRED = Decimal(100)


def _as_decimal(value: int | float | str | Decimal, what: str) -> Decimal:
    """Exact Decimal of a number (floats go through str, so 1.91 stays 1.91)."""
    if isinstance(value, bool):
        raise ValueError(f"{what} must be a number, got {value!r}")
    try:
        result = Decimal(str(value)) if isinstance(value, float) else Decimal(value)
    except (InvalidOperation, TypeError):
        raise ValueError(f"{what} must be a number, got {value!r}") from None
    if not result.is_finite():
        raise ValueError(f"{what} must be finite, got {value!r}")
    return result


def quantize_price(price: Decimal) -> Decimal:
    """Round a decimal price to the stored precision (4 places, half up); must stay above 1."""
    rounded = price.quantize(PRICE_QUANTUM, rounding=ROUND_HALF_UP)
    if rounded <= 1:
        raise ValueError(f"decimal price {price} is not above 1 at 4 decimal places")
    return rounded


def american_to_decimal(american: int | float | str | Decimal) -> Decimal:
    """American odds -> decimal price, e.g. +150 -> 2.5000, -200 -> 1.5000, -110 -> 1.9091."""
    value = _as_decimal(american, "American odds")
    if -100 < value < 100:
        raise ValueError(f"American odds must be <= -100 or >= +100, got {american!r}")
    if value > 0:
        return quantize_price(1 + value / _HUNDRED)
    return quantize_price(1 + _HUNDRED / -value)


def decimal_to_american(price: int | float | str | Decimal) -> Decimal:
    """Decimal price -> American odds (2 places), e.g. 2.5 -> +150.00, 1.5 -> -200.00."""
    value = _as_decimal(price, "decimal price")
    if value <= 1:
        raise ValueError(f"decimal price must be above 1, got {price!r}")
    american = (value - 1) * _HUNDRED if value >= 2 else -_HUNDRED / (value - 1)
    return american.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def to_decimal_price(price: int | float | str | Decimal, odds_format: str) -> Decimal:
    """A price in the given format ('decimal' or 'american') as a stored decimal price."""
    if odds_format == "american":
        return american_to_decimal(price)
    if odds_format == "decimal":
        return quantize_price(_as_decimal(price, "decimal price"))
    raise ValueError(f"unknown odds format {odds_format!r}; expected one of {ODDS_FORMATS}")


def implied_probability(price: int | float | str | Decimal) -> float:
    """Probability a decimal price implies, including the bookmaker margin: 1 / price."""
    value = _as_decimal(price, "decimal price")
    if value <= 1:
        raise ValueError(f"decimal price must be above 1, got {price!r}")
    return float(1 / value)


def overround(prices: Sequence[int | float | str | Decimal]) -> float:
    """Bookmaker margin of one market: sum of implied probabilities minus 1 (0.0476 = 4.76%)."""
    if len(prices) < 2:
        raise ValueError("a market needs at least two outcomes")
    return sum(implied_probability(p) for p in prices) - 1


def remove_margin_proportional(prices: Sequence[int | float | str | Decimal]) -> list[float]:
    """Fair probabilities of one market's outcomes: implied probabilities scaled to sum to 1."""
    if len(prices) < 2:
        raise ValueError("a market needs at least two outcomes")
    implied = [implied_probability(p) for p in prices]
    total = sum(implied)
    return [p / total for p in implied]
