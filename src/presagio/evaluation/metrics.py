"""Evaluation metrics, exactly as modeling design v1.2 6-1 defines them (pure functions).

Symbols: y = 1 if the home team won, p = p_home clipped to [1e-6, 1 - 1e-6] before any
formula, T = actual total, mu = predicted total mean, L = the reference total line that was
stored with the prediction. Log loss uses the natural log. Lower is better for log loss,
Brier, ECE, MAE and RMSE; a negative delta (model - reference) means the model is better.

Uncertainty is a game-level bootstrap: games are resampled with replacement (default 1,000
times, fixed seed) and the 90% interval is the 5th..95th percentile of the statistic.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

# Design 6-1: p is clipped to [PROB_EPS, 1 - PROB_EPS] before every formula.
PROB_EPS = 1e-6
# Design 6-1: ten equal-width bins [0, 0.1), ..., [0.9, 1].
CALIBRATION_BINS = 10
# Design 6-1 (proposal values, D-049): 1,000 resamples, 90% percentile interval, fixed seed.
DEFAULT_BOOTSTRAP = 1000
DEFAULT_CI_LEVEL = 0.90
DEFAULT_SEED = 0


def _check_lengths(*columns: Sequence[Any]) -> int:
    """Common length of the columns; ValueError when they differ or are empty."""
    lengths = {len(c) for c in columns}
    if len(lengths) != 1:
        raise ValueError(f"columns differ in length: {sorted(lengths)}")
    n = lengths.pop()
    if n == 0:
        raise ValueError("no games to score")
    return n


def _check_label(y: int | bool) -> int:
    """A binary outcome as 0/1; ValueError otherwise."""
    if y not in (0, 1):
        raise ValueError(f"outcome must be 0 or 1, got {y!r}")
    return int(y)


def clip_probability(p: float, eps: float = PROB_EPS) -> float:
    """p clipped to [eps, 1 - eps]; ValueError outside [0, 1] or for nan."""
    if not 0.0 <= p <= 1.0:  # also rejects nan
        raise ValueError(f"probability must be in [0, 1], got {p!r}")
    return min(max(float(p), eps), 1.0 - eps)


def game_log_loss(p: float, y: int | bool) -> float:
    """One game's log loss: -(y ln p + (1 - y) ln(1 - p)), p clipped."""
    p, y = clip_probability(p), _check_label(y)
    return -(y * math.log(p) + (1 - y) * math.log(1.0 - p))


def game_brier(p: float, y: int | bool) -> float:
    """One game's Brier score: (p - y)^2, p clipped."""
    return (clip_probability(p) - _check_label(y)) ** 2


def game_correct(p: float, y: int | bool) -> bool:
    """Whether the pick (home when p >= 0.5) matches the outcome."""
    return (clip_probability(p) >= 0.5) == bool(_check_label(y))


def accuracy(ps: Sequence[float], ys: Sequence[int | bool]) -> float:
    """mean(1[(p >= 0.5) = y]); p = 0.5 picks the home team."""
    n = _check_lengths(ps, ys)
    return sum(game_correct(p, y) for p, y in zip(ps, ys, strict=True)) / n


def log_loss(ps: Sequence[float], ys: Sequence[int | bool]) -> float:
    """-mean(y ln p + (1 - y) ln(1 - p)), natural log, p clipped."""
    n = _check_lengths(ps, ys)
    return sum(game_log_loss(p, y) for p, y in zip(ps, ys, strict=True)) / n


def brier(ps: Sequence[float], ys: Sequence[int | bool]) -> float:
    """mean((p - y)^2), p clipped."""
    n = _check_lengths(ps, ys)
    return sum(game_brier(p, y) for p, y in zip(ps, ys, strict=True)) / n


@dataclass(frozen=True)
class CalibrationBin:
    """One reliability-table row: games whose p fell in [lower, upper)."""

    lower: float
    upper: float
    n: int
    mean_p: float | None
    observed: float | None  # share of home wins; None for an empty bin


def calibration_bin_index(p: float, bins: int = CALIBRATION_BINS) -> int:
    """Index of p's bin among `bins` equal-width bins; the last bin includes 1."""
    return min(math.floor(clip_probability(p) * bins), bins - 1)


def calibration_table(
    ps: Sequence[float], ys: Sequence[int | bool], bins: int = CALIBRATION_BINS
) -> list[CalibrationBin]:
    """Per bin: n, mean p and the observed home win rate (all bins, empty ones included)."""
    _check_lengths(ps, ys)
    members: list[list[tuple[float, int]]] = [[] for _ in range(bins)]
    for p, y in zip(ps, ys, strict=True):
        members[calibration_bin_index(p, bins)].append((clip_probability(p), _check_label(y)))
    table = []
    for b, rows in enumerate(members):
        n = len(rows)
        table.append(
            CalibrationBin(
                lower=b / bins,
                upper=(b + 1) / bins,
                n=n,
                mean_p=sum(p for p, _ in rows) / n if n else None,
                observed=sum(y for _, y in rows) / n if n else None,
            )
        )
    return table


def expected_calibration_error(table: Sequence[CalibrationBin]) -> float:
    """ECE = sum_b (n_b / N) * |mean p_b - observed_b| over a calibration table."""
    total = sum(b.n for b in table)
    if total == 0:
        raise ValueError("no games to score")
    return sum(
        b.n / total * abs(b.mean_p - b.observed)
        for b in table
        if b.n and b.mean_p is not None and b.observed is not None
    )


def ece(ps: Sequence[float], ys: Sequence[int | bool], bins: int = CALIBRATION_BINS) -> float:
    """ECE of predictions straight from p and y."""
    return expected_calibration_error(calibration_table(ps, ys, bins))


def mae(means: Sequence[float], totals: Sequence[float]) -> float:
    """mean(|mu - T|)."""
    n = _check_lengths(means, totals)
    return sum(abs(m - t) for m, t in zip(means, totals, strict=True)) / n


def rmse(means: Sequence[float], totals: Sequence[float]) -> float:
    """sqrt(mean((mu - T)^2))."""
    n = _check_lengths(means, totals)
    return math.sqrt(sum((m - t) ** 2 for m, t in zip(means, totals, strict=True)) / n)


def over_outcome(total: float, line: float) -> int | None:
    """1 if T > L, 0 if T < L, None for a push (T = L)."""
    if total > line:
        return 1
    if total < line:
        return 0
    return None


@dataclass(frozen=True)
class OverScores:
    """Over-probability scores on the games that did not push."""

    n: int
    n_push: int
    log_loss: float | None
    brier: float | None


def over_scores(
    over_probs: Sequence[float], totals: Sequence[float], lines: Sequence[float]
) -> OverScores:
    """Log loss and Brier of P(T > L) with y = 1[T > L]; pushes (T = L) are left out."""
    _check_lengths(over_probs, totals, lines)
    ps: list[float] = []
    ys: list[int] = []
    pushes = 0
    for p, total, line in zip(over_probs, totals, lines, strict=True):
        outcome = over_outcome(total, line)
        if outcome is None:
            pushes += 1
            continue
        ps.append(p)
        ys.append(outcome)
    if not ps:
        return OverScores(0, pushes, None, None)
    return OverScores(len(ps), pushes, log_loss(ps, ys), brier(ps, ys))


def brier_skill_score(brier_model: float, brier_reference: float) -> float:
    """1 - Brier_model / Brier_reference; positive means the model is better."""
    if brier_reference <= 0:
        raise ValueError(f"reference Brier must be > 0, got {brier_reference!r}")
    return 1.0 - brier_model / brier_reference


@dataclass(frozen=True)
class PairedScores:
    """Model vs reference (e.g. market) on the same games."""

    n: int
    log_loss_model: float
    log_loss_reference: float
    brier_model: float
    brier_reference: float

    @property
    def delta_log_loss(self) -> float:
        """LL_model - LL_reference (negative = model better)."""
        return self.log_loss_model - self.log_loss_reference

    @property
    def delta_brier(self) -> float:
        """Brier_model - Brier_reference (negative = model better)."""
        return self.brier_model - self.brier_reference

    @property
    def brier_skill(self) -> float:
        """1 - Brier_model / Brier_reference."""
        return brier_skill_score(self.brier_model, self.brier_reference)


def paired_scores(
    p_model: Sequence[float], p_reference: Sequence[float], ys: Sequence[int | bool]
) -> PairedScores:
    """Log loss and Brier of two forecasts of the same games (same set by construction)."""
    n = _check_lengths(p_model, p_reference, ys)
    return PairedScores(
        n=n,
        log_loss_model=log_loss(p_model, ys),
        log_loss_reference=log_loss(p_reference, ys),
        brier_model=brier(p_model, ys),
        brier_reference=brier(p_reference, ys),
    )


@dataclass(frozen=True)
class Interval:
    """A bootstrap percentile interval around a point estimate."""

    estimate: float
    lower: float
    upper: float
    level: float
    n_boot: int

    def excludes_zero(self) -> bool:
        """True when the whole interval is on one side of 0."""
        return self.lower > 0 or self.upper < 0


def bootstrap_indices(
    n: int, n_boot: int = DEFAULT_BOOTSTRAP, seed: int = DEFAULT_SEED
) -> np.ndarray:
    """n_boot x n game indices drawn with replacement (numpy default_rng, fixed seed)."""
    if n <= 0 or n_boot <= 0:
        raise ValueError(f"need n > 0 and n_boot > 0, got n={n}, n_boot={n_boot}")
    return np.random.default_rng(seed).integers(0, n, size=(n_boot, n))


def percentile_interval(
    samples: Sequence[float] | np.ndarray, level: float = DEFAULT_CI_LEVEL
) -> tuple[float, float]:
    """(lower, upper) percentiles of the samples for a central `level` interval (linear)."""
    if not 0 < level < 1:
        raise ValueError(f"level must be in (0, 1), got {level!r}")
    tail = round((1.0 - level) / 2.0 * 100.0, 9)  # 0.90 -> exactly 5, not 4.999...
    lower, upper = np.percentile(np.asarray(samples, dtype=float), [tail, 100.0 - tail])
    return float(lower), float(upper)


def bootstrap_interval(
    columns: Sequence[Sequence[float]],
    statistic: Callable[..., Any],
    *,
    n_boot: int = DEFAULT_BOOTSTRAP,
    seed: int = DEFAULT_SEED,
    level: float = DEFAULT_CI_LEVEL,
) -> Interval:
    """Interval of statistic(*column means), resampling games (rows) jointly across columns.

    Every column holds one value per game (e.g. per-game log loss of the model and of the
    market); one resample draws the same games for all columns, so paired statistics such
    as deltas and skill scores keep their pairing. `statistic` gets the column means (numpy
    arrays of length n_boot inside the bootstrap, floats for the point estimate).
    """
    n = _check_lengths(*columns)
    values = [np.asarray(c, dtype=float) for c in columns]
    estimate = float(statistic(*(float(v.mean()) for v in values)))
    index = bootstrap_indices(n, n_boot, seed)
    with np.errstate(divide="ignore", invalid="ignore"):
        samples = np.asarray(statistic(*(v[index].mean(axis=1) for v in values)), dtype=float)
    lower, upper = percentile_interval(samples, level)
    return Interval(estimate, lower, upper, level, n_boot)


def bootstrap_mean_interval(
    values: Sequence[float],
    *,
    n_boot: int = DEFAULT_BOOTSTRAP,
    seed: int = DEFAULT_SEED,
    level: float = DEFAULT_CI_LEVEL,
) -> Interval:
    """Interval of the mean of per-game values (log loss, Brier, deltas, CLV return...)."""
    return bootstrap_interval([values], lambda m: m, n_boot=n_boot, seed=seed, level=level)
