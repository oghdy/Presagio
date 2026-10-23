"""Metrics of design 6-1 against hand-computed values."""

import math

import numpy as np
import pytest

from presagio.evaluation.metrics import (
    PROB_EPS,
    accuracy,
    bootstrap_indices,
    bootstrap_interval,
    bootstrap_mean_interval,
    brier,
    brier_skill_score,
    calibration_bin_index,
    calibration_table,
    clip_probability,
    ece,
    expected_calibration_error,
    game_log_loss,
    log_loss,
    mae,
    over_outcome,
    over_scores,
    paired_scores,
    percentile_interval,
    rmse,
)

PS = [0.8, 0.4, 0.5]
YS = [1, 0, 0]


def test_accuracy_counts_p_half_as_a_home_pick() -> None:
    """0.8->home (won), 0.4->away (home lost), 0.5->home (home lost): 2/3."""
    assert accuracy(PS, YS) == pytest.approx(2 / 3)


def test_log_loss_by_hand() -> None:
    """-(ln 0.8 + ln 0.6 + ln 0.5) / 3 = (0.22314 + 0.51083 + 0.69315) / 3 = 0.47571."""
    expected = (0.2231435513 + 0.5108256238 + 0.6931471806) / 3
    assert log_loss(PS, YS) == pytest.approx(expected, abs=1e-9)
    assert log_loss(PS, YS) == pytest.approx(0.4757054519, abs=1e-9)


def test_log_loss_clips_certain_wrong_forecasts() -> None:
    """p = 1 for a home loss costs -ln(1e-6) = 13.8155..., not infinity."""
    assert game_log_loss(1.0, 0) == pytest.approx(-math.log(PROB_EPS))
    assert game_log_loss(0.0, 1) == pytest.approx(13.815510557964274)
    assert clip_probability(0.0) == PROB_EPS and clip_probability(1.0) == 1 - PROB_EPS


def test_brier_by_hand() -> None:
    """(0.2^2 + 0.4^2 + 0.5^2) / 3 = (0.04 + 0.16 + 0.25) / 3 = 0.15."""
    assert brier(PS, YS) == pytest.approx(0.15)


def test_invalid_inputs_raise() -> None:
    """Probabilities outside [0, 1], labels outside {0, 1}, empty and ragged columns."""
    with pytest.raises(ValueError):
        log_loss([1.2], [1])
    with pytest.raises(ValueError):
        brier([0.5], [2])
    with pytest.raises(ValueError):
        accuracy([], [])
    with pytest.raises(ValueError):
        log_loss([0.5, 0.5], [1])
    with pytest.raises(ValueError):
        clip_probability(float("nan"))


def test_calibration_table_and_ece_by_hand() -> None:
    """Bins 0: {0.05: lost}; 1: {0.15 won, 0.12 lost}; 9: {0.95 won, 0.97 won}.

    ECE = 1/5 * |0.05 - 0| + 2/5 * |0.135 - 0.5| + 2/5 * |0.96 - 1|
        = 0.01 + 0.146 + 0.016 = 0.172.
    """
    ps = [0.05, 0.15, 0.12, 0.95, 0.97]
    ys = [0, 1, 0, 1, 1]
    table = calibration_table(ps, ys)
    assert len(table) == 10
    assert [b.n for b in table] == [1, 2, 0, 0, 0, 0, 0, 0, 0, 2]
    assert table[1].mean_p == pytest.approx(0.135) and table[1].observed == 0.5
    assert table[2].mean_p is None and table[2].observed is None
    assert table[9].mean_p == pytest.approx(0.96) and table[9].observed == 1.0
    assert expected_calibration_error(table) == pytest.approx(0.172)
    assert ece(ps, ys) == pytest.approx(0.172)


def test_calibration_bins_are_half_open_and_last_is_closed() -> None:
    """0.1 starts bin 1, 0.0999 is bin 0, 0.9 and 1.0 are bin 9."""
    assert calibration_bin_index(0.0999) == 0
    assert calibration_bin_index(0.1) == 1
    assert calibration_bin_index(0.3) == 3
    assert calibration_bin_index(0.7) == 7
    assert calibration_bin_index(0.9) == 9
    assert calibration_bin_index(1.0) == 9


def test_total_mae_and_rmse_by_hand() -> None:
    """Errors 10 and 5: MAE 7.5, RMSE sqrt((100 + 25) / 2) = 7.9057."""
    assert mae([220, 230], [210, 235]) == 7.5
    assert rmse([220, 230], [210, 235]) == pytest.approx(math.sqrt(62.5))


def test_over_scores_leave_pushes_out() -> None:
    """221 > 220.5 (over), 219 < 220.5 (under), 220 = 220 (push, excluded).

    LL = -(ln 0.6 + ln 0.7) / 2 = (0.51083 + 0.35667) / 2 = 0.43375; Brier = (0.16 + 0.09) / 2.
    """
    scores = over_scores([0.6, 0.3, 0.9], [221, 219, 220], [220.5, 220.5, 220.0])
    assert (scores.n, scores.n_push) == (2, 1)
    assert scores.log_loss == pytest.approx(0.4337502838, abs=1e-9)
    assert scores.brier == pytest.approx(0.125)
    assert over_outcome(220, 220) is None
    assert over_scores([0.5], [220], [220]).log_loss is None


def test_paired_deltas_and_brier_skill_by_hand() -> None:
    """Model (0.7, 0.4) vs market (0.6, 0.5) on outcomes (1, 0).

    LL_model = (0.35667 + 0.51083) / 2 = 0.43375, LL_market = (0.51083 + 0.69315) / 2 =
    0.60199, dLL = -0.16824. Brier 0.125 vs 0.205, dBrier = -0.08, BSS = 1 - 0.125/0.205.
    """
    scores = paired_scores([0.7, 0.4], [0.6, 0.5], [1, 0])
    assert scores.n == 2
    assert scores.log_loss_model == pytest.approx(0.4337502838, abs=1e-9)
    assert scores.log_loss_reference == pytest.approx(0.6019864021, abs=1e-9)
    assert scores.delta_log_loss == pytest.approx(-0.1682361183, abs=1e-9)
    assert scores.delta_brier == pytest.approx(-0.08)
    assert scores.brier_skill == pytest.approx(1 - 0.125 / 0.205)
    assert brier_skill_score(0.2, 0.25) == pytest.approx(0.2)
    with pytest.raises(ValueError):
        brier_skill_score(0.2, 0.0)


def test_percentile_interval_is_numpy_linear() -> None:
    """5th and 95th percentiles of 0..100 are 5 and 95."""
    assert percentile_interval(list(range(101)), 0.90) == (5.0, 95.0)


def test_bootstrap_is_deterministic_and_resamples_games_with_replacement() -> None:
    """Same seed -> same draws; draws are indices in [0, n) and repeat (replacement)."""
    a = bootstrap_indices(50, 1000, seed=0)
    b = bootstrap_indices(50, 1000, seed=0)
    assert a.shape == (1000, 50) and (a == b).all()
    assert a.min() >= 0 and a.max() < 50
    assert any(len(set(row)) < 50 for row in a)  # with replacement
    assert not (bootstrap_indices(50, 1000, seed=1) == a).all()


def test_bootstrap_two_games_by_hand() -> None:
    """Values (0, 1): resampled means are 0, 0.5, 1 with probabilities 1/4, 1/2, 1/4, so the
    5th..95th percentile interval is [0, 1] and the estimate 0.5."""
    interval = bootstrap_mean_interval([0.0, 1.0], n_boot=1000, seed=0)
    assert (interval.estimate, interval.lower, interval.upper) == (0.5, 0.0, 1.0)
    constant = bootstrap_mean_interval([0.25] * 10)
    assert (constant.lower, constant.upper) == (0.25, 0.25)
    assert constant.excludes_zero()
    assert not bootstrap_mean_interval([-1.0, 1.0]).excludes_zero()


def test_bootstrap_interval_matches_an_explicit_loop() -> None:
    """The vectorised paired bootstrap equals a plain loop over the same index draws."""
    rng = np.random.default_rng(7)
    model = rng.uniform(0.4, 0.8, 40)
    market = rng.uniform(0.4, 0.8, 40)
    interval = bootstrap_interval([model, market], lambda a, b: a - b, n_boot=500, seed=3)
    index = bootstrap_indices(40, 500, seed=3)
    loop = [model[row].mean() - market[row].mean() for row in index]
    assert (interval.lower, interval.upper) == pytest.approx(percentile_interval(loop))
    assert interval.estimate == pytest.approx(model.mean() - market.mean())
    assert interval.n_boot == 500 and interval.level == 0.90
