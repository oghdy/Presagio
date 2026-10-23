"""The walk-forward runner on the temp league DB: time rules and leakage tests at runner level
(design 9-3 T1, T2, T4, T8, T10, T12, T14 interface part), T1/T2 for every registered
experiment (P-033 criterion 2), the training cross-check and the season lower bound of the
training window (P-032 review (a), (b))."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Connection, text

from presagio.evaluation import walkforward
from presagio.evaluation.data import load_games, load_prediction_quotes
from presagio.evaluation.dummy import ConstantPredictor, HomeRateLearner
from presagio.evaluation.experiments import EXPERIMENTS
from presagio.evaluation.holdout import HoldoutLockedError
from presagio.evaluation.market import prediction_market
from presagio.evaluation.scoring import score_run
from presagio.evaluation.timing import backtest_as_of, et_date
from presagio.evaluation.walkforward import (
    SKIP_AFTER_NOW,
    SKIP_MIN_TRAINING,
    SKIP_NOT_PREDICTED,
    SKIP_STARTED,
    LeakageGuardError,
    Prediction,
    PredictionContext,
    PredictorOutputError,
    ScheduleOnlyTrainingSource,
    TargetGame,
    TrainingMatrix,
    TrainingRow,
    WalkForwardConfig,
    WalkForwardResult,
    _check_prediction_times,
    _check_training,
    run_walk_forward,
)
from presagio.models.baselines import MarketBaseline

from .league import (
    CUP_FINAL,
    LIVE_AVAILABLE,
    LIVE_NOT_FINAL,
    LIVE_PRED_ONLY,
    LIVE_UNMATCHED,
    T4_LATE_GAME,
    T4_NEXT_DAY_GAME,
    League,
    et,
    insert_games,
    insert_snapshot,
    insert_stats,
)

DEV = ("2023-24",)
PIVOT = date(2024, 2, 15)  # a game date in the middle of 2023-24
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
# Predictors whose output depends on the visible results (T1/T2 power check); B3 reads only
# the market, and 2023-24 has none (D-026), so it predicts nothing there.
PROBE = "probe"
RESULT_USERS = (PROBE, "H-00", "B0", "B1")
ALL_PREDICTORS = (PROBE, *sorted(EXPERIMENTS))


class Probe:
    """A non-learning predictor whose output depends on everything it can see.

    p_home mixes the visible home win rate with the market; total_mean is the visible mean
    total plus a team-specific term, so any extra or changed visible result changes it.
    """

    name = "probe"
    trains = False
    feature_version = None

    def __init__(self) -> None:
        """Record what each day showed."""
        self.seen: dict[date, tuple[str, ...]] = {}

    def fit(self, training: TrainingMatrix) -> None:
        """Never called for a non-learning predictor."""
        raise AssertionError("fit called on a non-learning predictor")

    def predict(
        self, as_of: datetime, games: Sequence[TargetGame], context: PredictionContext
    ) -> Mapping[str, Prediction]:
        """Predictions from visible results and the R3 market only."""
        results = context.results()
        self.seen[context.target_date] = tuple(r.game_id for r in results)
        n = len(results)
        wins = sum(r.home_won for r in results)
        mean = sum(r.total for r in results) / n if n else 200.0
        out = {}
        for g in games:
            team = sum(
                r.home_score if r.home_team_id == g.home_team_id else r.away_score
                for r in results
                if g.home_team_id in (r.home_team_id, r.away_team_id)
            )
            p = (wins + 1) / (n + 2)
            market = context.market(g.game_id)
            if market is not None and market.home is not None:
                p = (p + market.home.p_home) / 2
            out[g.game_id] = Prediction(p_home=p, total_mean=mean + team / 1000)
        return out


def make(name: str) -> Any:
    """A fresh predictor: the probe or a registered experiment's."""
    return Probe() if name == PROBE else EXPERIMENTS[name].make()


def run(conn: Connection, predictor: Any = None, **config: Any) -> WalkForwardResult:
    """run_walk_forward with defaults for the tests (a predictor name is accepted too)."""
    seasons = config.pop("seasons", DEV)
    now = config.pop("now", NOW)
    if isinstance(predictor, str):
        predictor = make(predictor)
    return run_walk_forward(
        conn,
        predictor if predictor is not None else Probe(),
        WalkForwardConfig(seasons=seasons, **config),
        now=now,
    )


def outputs(result: WalkForwardResult, until: date | None = None) -> list[tuple]:
    """Everything a prediction carries, for days up to `until`."""
    return [
        (
            p.game.game_id,
            p.as_of,
            p.p_home,
            p.total_mean,
            p.train_cutoff,
            p.reference_line,
            p.market.home.p_home if p.market.home else None,
            p.reference_bookmaker,
            p.market.reference(),
        )
        for p in result.predictions
        if until is None or p.game.et_date <= until
    ]


def expected_visible(league: League, day: date, lag: timedelta = timedelta(hours=3)) -> set:
    """Independent R1/R2 oracle from the fixture rows (cup final has no stats)."""
    as_of = backtest_as_of(day)
    return {
        g["game_id"]
        for g in league.games.values()
        if g["status_code"] == 3
        and g["game_id"] != CUP_FINAL
        and et_date(g["start_time_utc"]) < day
        and g["start_time_utc"] + lag <= as_of
    }


def test_predicts_every_target_game_once_at_07_utc(conn: Connection, league: League) -> None:
    """2023-24: every regular, play-in and playoff game, never the Cup final; as_of = 07:00
    UTC of the game's ET date, before tip-off; warm-up before 2024-01-01 not reported."""
    result = run(conn)
    ids = [p.game.game_id for p in result.predictions]
    expected = [g for g in league.ids("2023-24") if g != CUP_FINAL]
    assert sorted(ids) == sorted(expected) and len(ids) == len(set(ids)) == 174
    for p in result.predictions:
        assert p.as_of == backtest_as_of(p.game.et_date) < p.game.start_time_utc
        assert (p.snapshot_kind, p.origin) == ("daily", "backtest")
        assert p.reported == (p.game.et_date >= date(2024, 1, 1))
        assert p.train_cutoff is None or p.train_cutoff <= p.as_of
    assert result.holdout.unlocked_reason is None


def test_context_shows_exactly_the_r1_r2_results(conn: Connection, league: League) -> None:
    """Every day the predictor saw exactly the oracle's set: D(h) < d, start + 3 h <= as_of,
    final with stats, no Cup final."""
    probe = Probe()
    run(conn, probe)
    assert set(probe.seen) == {p.game.et_date for p in run(conn).predictions}
    assert len(probe.seen) == 89
    for day, seen in probe.seen.items():
        assert set(seen) == expected_visible(league, day), day


def extreme_game(game_id: str, season: str, start: datetime, home: int, away: int) -> dict:
    """A final game with an absurd score."""
    return {
        "game_id": game_id,
        "season": season,
        "start_time_utc": start,
        "status_code": 3,
        "status_text": "Final",
        "home_team_id": home,
        "away_team_id": away,
        "home_score": 200,
        "away_score": 40,
        "is_neutral": False,
        "source": "leaguegamelog",
    }


def make_extreme(conn: Connection, where: str, params: dict) -> int:
    """Set home 300 / away 0 and absurd stats on the games matching `where`."""
    changed = conn.execute(
        text(f"UPDATE games SET home_score = 300, away_score = 0 WHERE {where}"), params
    ).rowcount
    conn.execute(
        text(
            f"""
            UPDATE team_game_stats SET pts = CASE WHEN is_home THEN 300 ELSE 0 END,
                   fga = 300, fta = 100, tov = 90
            WHERE game_id IN (SELECT game_id FROM games WHERE {where})
            """
        ),
        params,
    )
    return changed


def assert_no_odds_season_run(result: WalkForwardResult, n: int) -> None:
    """B3 over 2023-24: nothing predicted, every one of the n targets recorded as
    no_odds_season."""
    assert result.predictions == [] and result.skipped[SKIP_NOT_PREDICTED] == n
    assert {m.market_status for m in result.not_predicted} == {"no_odds_season"}
    assert len(result.not_predicted) == n


@pytest.mark.parametrize("name", ALL_PREDICTORS)
def test_t1_time_travel_changes_after_as_of_leave_predictions_unchanged(
    conn: Connection, league: League, name: str
) -> None:
    """T1 at runner level, for the probe and every registered experiment. After predicting,
    (i) add ten extreme games after the pivot date, (ii) change the pivot games' own scores
    and stats (the target g and the same-date other game). Predictions up to the pivot date
    stay identical; later ones change for every predictor that reads results (power)."""
    before = run(conn, name)
    a, b, c, d = league.teams
    raw = conn.execute(text("SELECT min(raw_response_id) FROM raw_api_responses")).scalar_one()
    added = [
        extreme_game(f"00223009{50 + i}", "2023-24", et(date(2024, 4, 26) + timedelta(i), 20), a, b)
        for i in range(10)
    ]
    insert_games(conn, added, raw)
    for game in added:
        insert_stats(conn, game, raw)
    pivot_window = {"lo": et(PIVOT, 0), "hi": et(PIVOT + timedelta(days=1), 0)}
    assert make_extreme(conn, "start_time_utc >= :lo AND start_time_utc < :hi", pivot_window) == 2
    after = run(conn, name)
    assert outputs(after, PIVOT) == outputs(before, PIVOT)
    if name in RESULT_USERS:
        assert outputs(after) != outputs(before)
    else:
        assert_no_odds_season_run(before, 174)
        assert_no_odds_season_run(after, 184)  # the ten added games are targets too


@pytest.mark.parametrize("name", ALL_PREDICTORS)
def test_t2_future_injection_leaves_past_predictions_unchanged(
    conn: Connection, league: League, name: str
) -> None:
    """T2 at runner level, for the probe and every registered experiment: every game from
    the pivot date on (the target included) becomes 300-0 with absurd stats; predictions up
    to the pivot date are identical."""
    before = run(conn, name)
    changed = make_extreme(conn, "status_code = 3 AND start_time_utc >= :lo", {"lo": et(PIVOT, 0)})
    assert changed > 50
    after = run(conn, name)
    assert outputs(after, PIVOT) == outputs(before, PIVOT)
    if name not in RESULT_USERS:
        assert_no_odds_season_run(after, 174)
        return
    later = [o for o in outputs(after) if o not in outputs(before)]
    assert later and all(league.games[o[0]]["start_time_utc"] > et(PIVOT, 23, 59) for o in later)


def flip_to_away_wins(conn: Connection, where: str, params: dict) -> int:
    """Home 0 / away 300 (and stats to match) on the games matching `where`."""
    changed = conn.execute(
        text(f"UPDATE games SET home_score = 0, away_score = 300 WHERE {where}"), params
    ).rowcount
    conn.execute(
        text(
            f"""
            UPDATE team_game_stats SET pts = CASE WHEN is_home THEN 0 ELSE 300 END
            WHERE game_id IN (SELECT game_id FROM games WHERE {where})
            """
        ),
        params,
    )
    return changed


def day_outputs(result: WalkForwardResult, day: date) -> list[tuple[str, float, datetime]]:
    """(game_id, p_home, train_cutoff) of one ET date's predictions."""
    return [
        (p.game.game_id, p.p_home, p.train_cutoff)
        for p in result.predictions
        if p.game.et_date == day
    ]


@pytest.mark.parametrize("name", ["B0", "B1"])
def test_same_day_results_never_enter_that_days_baseline(
    conn: Connection, league: League, name: str
) -> None:
    """Criterion 2 off-by-one for the season-cumulative baselines: the pivot day's two games
    (the target and the other game of the same ET date) were home wins; flipping both to
    away wins leaves the pivot day's predictions unchanged, while the next game date's
    predictions change (so the day-d results are in by d + 1: the test has power)."""
    before = run(conn, name)
    for gid, _, _ in day_outputs(before, PIVOT):
        game = league.games[gid]
        assert game["home_score"] > game["away_score"]
    window = {"lo": et(PIVOT, 0), "hi": et(PIVOT + timedelta(days=1), 0)}
    assert flip_to_away_wins(conn, "start_time_utc >= :lo AND start_time_utc < :hi", window) == 2
    after = run(conn, name)
    assert len(day_outputs(before, PIVOT)) == 2
    assert day_outputs(after, PIVOT) == day_outputs(before, PIVOT)
    next_day = PIVOT + timedelta(days=2)
    assert day_outputs(after, next_day) != day_outputs(before, next_day)
    assert outputs(after, PIVOT) == outputs(before, PIVOT)


def test_train_cutoff_is_the_last_visible_result_or_as_of(conn: Connection) -> None:
    """Non-learning baselines: train_cutoff = latest available_at of the visible results
    (as_of on the first day, before any result); B3 reads no result, so always as_of."""
    b0 = run(conn, "B0")
    first = b0.predictions[0]
    assert first.train_cutoff == first.as_of
    for p in b0.predictions:
        assert p.train_cutoff <= p.as_of < p.game.start_time_utc
    seen = {p.game.game_id: p for p in run(conn, Probe()).predictions}
    assert any(p.train_cutoff < p.as_of for p in b0.predictions)
    assert all(seen[p.game.game_id].train_cutoff == p.train_cutoff for p in b0.predictions)
    b3 = run(conn, "B3", seasons=("2026-27",))
    assert b3.predictions and all(p.train_cutoff == p.as_of for p in b3.predictions)


def test_b3_predicts_only_games_with_a_market_and_records_the_reason(conn: Connection) -> None:
    """B3 over 2026-27: the two games with a prediction-time market are predicted with the
    market probability and prediction-time status `available`; the unmatched and the
    quote-less game are not predicted, with unmatched_event / no_snapshot_at_as_of."""
    result = run(conn, "B3", seasons=("2026-27",))
    by_id = {p.game.game_id: p for p in result.predictions}
    assert set(by_id) == {LIVE_AVAILABLE, LIVE_PRED_ONLY}
    for p in by_id.values():
        assert p.market.home is not None and p.p_home == p.market.home.p_home
        assert p.market_status == "available"
    reasons = {n.game.game_id: n.market_status for n in result.not_predicted}
    assert reasons == {LIVE_UNMATCHED: "unmatched_event", LIVE_NOT_FINAL: "no_snapshot_at_as_of"}
    assert result.skipped[SKIP_NOT_PREDICTED] == 2
    probe = run(conn, Probe(), seasons=("2026-27",))
    statuses = {p.game.game_id: p.market_status for p in probe.predictions}
    assert statuses == {
        LIVE_UNMATCHED: "unmatched_event",
        LIVE_AVAILABLE: "available",
        LIVE_PRED_ONLY: "available",  # pred_only is the evaluation's verdict, not as_of's
        LIVE_NOT_FINAL: "no_snapshot_at_as_of",
    }
    assert {p.market_status for p in run(conn, Probe()).predictions} == {"no_odds_season"}


@pytest.mark.parametrize(
    ("lag", "visible"),
    [
        (timedelta(hours=3), True),  # 04:00 UTC + 3 h = 07:00 UTC = as_of: inclusive
        (timedelta(hours=3, seconds=1), False),
        (timedelta(hours=2), True),
    ],
)
def test_t4_lag_boundary_at_runner_level(conn: Connection, lag: timedelta, visible: bool) -> None:
    """T4: the 2024-01-10 23:00 ET game (04:00 UTC on 01-11) feeds 2024-01-11 predictions only
    while start + lag <= as_of (07:00 UTC); the setting changes what the predictor sees."""
    probe = Probe()
    run(conn, probe, availability_lag=lag)
    seen = probe.seen[date(2024, 1, 11)]
    assert (T4_LATE_GAME in seen) is visible
    assert T4_NEXT_DAY_GAME not in seen


def test_t10_holdout_is_refused_by_the_runner(conn: Connection, league: League) -> None:
    """T10: 2025-26 without a reason raises before anything runs; with one it runs and the
    reason is returned for model_versions.holdout_unlocked_reason."""
    with pytest.raises(HoldoutLockedError, match="holdout"):
        run(conn, seasons=("2024-25", "2025-26"))
    result = run(conn, seasons=("2025-26",), unlock_holdout="H-08 final model")
    assert result.holdout.unlocked_reason == "H-08 final model"
    assert {p.game.season for p in result.predictions} == {"2025-26"}


def test_holdout_results_may_feed_state_without_unlocking(conn: Connection, league: League) -> None:
    """Predicting 2026-27 reads 2025-26 results as inputs (6-3 item 2) but predicts and
    scores no 2025-26 game."""
    probe = Probe()
    result = run(conn, probe, seasons=("2026-27",))
    holdout_ids = set(league.ids("2025-26"))
    assert holdout_ids <= set(probe.seen[date(2026, 10, 20)])
    assert not any(p.game.season == "2025-26" for p in result.predictions)
    assert result.holdout.holdout_seasons == ()


def test_now_skips_days_whose_as_of_is_in_the_future(conn: Connection) -> None:
    """With now = 2026-10-22 08:00 UTC the 10-23 game is not predicted."""
    result = run(conn, seasons=("2026-27",), now=datetime(2026, 10, 22, 8, 0, tzinfo=UTC))
    assert {p.game.game_id for p in result.predictions} == {
        "0022600001",
        LIVE_AVAILABLE,
        LIVE_PRED_ONLY,
    }
    assert result.skipped[SKIP_AFTER_NOW] == 1


def test_prediction_time_market_is_r3_not_the_close(conn: Connection) -> None:
    """The 10-21 game's market at as_of is the 10-20 21:45 ET capture: consensus of three
    books (median = draftkings 0.461538) and line L = 220.5, not the closing 222.5."""
    result = run(conn, seasons=("2026-27",))
    by_id = {p.game.game_id: p for p in result.predictions}
    available = by_id[LIVE_AVAILABLE]
    assert available.market.home is not None
    assert available.market.home.source == "consensus"
    assert available.market.home.p_home == pytest.approx((1 / 2.10) / (1 / 2.10 + 1 / 1.80))
    assert available.market.home.captured_at == et(date(2026, 10, 20), 21, 45)
    assert available.reference_line == 220.5 and available.reference_bookmaker == "draftkings"
    pred_only = by_id[LIVE_PRED_ONLY].market.home
    assert pred_only is not None and pred_only.source == "draftkings"
    assert pred_only.p_home == pytest.approx(0.5)
    assert by_id[LIVE_NOT_FINAL].market.home is None


def test_r3_sql_boundary_includes_as_of_and_excludes_later(
    conn: Connection, league: League
) -> None:
    """T6 data part on the DB: draftkings h2h at as_of - 1 min (1.90/2.00), = as_of
    (1.95/1.95), + 1 min (2.50/1.60). The query returns the first two; the market is 0.5."""
    game = league.games[LIVE_NOT_FINAL]
    as_of = backtest_as_of(et_date(game["start_time_utc"]))
    raw = conn.execute(text("SELECT min(raw_response_id) FROM raw_api_responses")).scalar_one()
    event = conn.execute(
        text(
            """
            INSERT INTO odds_events (source, source_event_id, home_team_name, away_team_name,
                commence_time, game_id, match_method, matched_at, fetched_at, raw_response_id)
            VALUES ('the_odds_api', 'evt-t6', 'H', 'A', :start, :gid, 'alias+time', :at, :at,
                :raw)
            RETURNING odds_event_id
            """
        ),
        {"start": game["start_time_utc"], "gid": LIVE_NOT_FINAL, "at": as_of, "raw": raw},
    ).scalar_one()
    for offset, home, away in ((-60, "1.90", "2.00"), (0, "1.95", "1.95"), (60, "2.50", "1.60")):
        at = as_of + timedelta(seconds=offset)
        for side, price in (("home", home), ("away", away)):
            insert_snapshot(
                conn, event, game["start_time_utc"], at, "draftkings", "h2h", side, price, raw
            )
    quotes = load_prediction_quotes(conn, [LIVE_NOT_FINAL], as_of)[LIVE_NOT_FINAL]
    assert max(q.captured_at for q in quotes) == as_of and len(quotes) == 4
    market = prediction_market(quotes, as_of)
    assert market.home is not None and market.home.p_home == pytest.approx(0.5)
    assert market.home.captured_at == as_of


@pytest.mark.parametrize("name", ALL_PREDICTORS)
def test_t12_same_run_twice_gives_identical_predictions(conn: Connection, name: str) -> None:
    """T12 in memory (the store checks it against the database): two runs, identical values."""
    seasons = ("2026-27",) if name == "B3" else DEV
    first, second = run(conn, name, seasons=seasons), run(conn, name, seasons=seasons)
    assert outputs(first) == outputs(second) and (first.predictions or name == "B3")


def test_t8_learning_predictor_refits_weekly_on_earlier_games(
    conn: Connection, league: League
) -> None:
    """T8 with the learning dummy over 2023-24 and 2024-25: nothing predicted in 2023-24
    (minimum training), refits at least 7 days apart, every training game before its fit
    cutoff and available at it, train_cutoff <= as_of < tip-off for every prediction."""
    learner = HomeRateLearner()
    result = run(conn, learner, seasons=("2023-24", "2024-25"))
    assert {p.game.season for p in result.predictions} == {"2024-25"}
    assert result.skipped[SKIP_MIN_TRAINING] == 174
    first_day = min(p.game.et_date for p in result.predictions)
    assert result.refits[0].fit_cutoff == first_day == date(2024, 10, 22)
    assert result.refits[0].n_rows == 174  # the whole of 2023-24 (expanding window)
    cutoffs = [r.fit_cutoff for r in result.refits]
    gaps = [(b - a).days for a, b in zip(cutoffs, cutoffs[1:], strict=False)]
    assert gaps and min(gaps) >= 7
    sizes = [r.n_rows for r in result.refits]
    assert sizes == sorted(sizes)
    for fit in learner.fits:
        for row in fit.rows:
            assert row.et_date < fit.fit_cutoff and row.available_at <= fit.fit_as_of
    for refit in result.refits:
        assert refit.max_available_at is not None and refit.max_available_at <= refit.fit_as_of
    for p in result.predictions:
        assert p.train_cutoff is not None and p.train_cutoff <= p.as_of < p.game.start_time_utc
    assert all(p.reported for p in result.predictions)


def test_t14_training_rows_use_their_own_as_of_and_ignore_later_results(
    conn: Connection, league: League
) -> None:
    """training_matrix(feature_version, fit_cutoff) is the only door: every row's features are
    as of the row's own prediction date (before its tip-off); changing a training game's
    score changes its label, never any feature row."""
    cutoff = date(2024, 3, 1)
    first = ScheduleOnlyTrainingSource(load_games(conn)).training_matrix("dummy-fv0", cutoff)
    assert first.rows and first.fit_as_of == backtest_as_of(cutoff)
    for row in first.rows:
        assert row.feature_as_of == backtest_as_of(row.et_date) < row.start_time_utc
    target = first.rows[10].game_id
    conn.execute(
        text("UPDATE games SET home_score = 1, away_score = 300 WHERE game_id = :g"), {"g": target}
    )
    second = ScheduleOnlyTrainingSource(load_games(conn)).training_matrix("dummy-fv0", cutoff)
    assert [(r.game_id, dict(r.features)) for r in second.rows] == [
        (r.game_id, dict(r.features)) for r in first.rows
    ]
    labels = {r.game_id: r.home_won for r in second.rows}
    assert labels[target] is False


class BadSource:
    """A training source that breaks one rule, to show the runner's guard catches it."""

    def __init__(self, conn: Connection, mode: str) -> None:
        """mode: 'refit_as_of' (features at the refit date), 'future' (D(h) moved to the
        cutoff date) or 'same_day' (a game of the cutoff date whose only broken rule is
        D(h) < fit_cutoff: features as of its own date, before its tip-off, and a reported
        available_at at the refit as_of)."""
        self.inner = ScheduleOnlyTrainingSource(load_games(conn), train_start_season="2023-24")
        self.mode = mode

    def training_matrix(self, feature_version: str, fit_cutoff: date) -> TrainingMatrix:
        """The honest matrix with one row corrupted."""
        good = self.inner.training_matrix(feature_version, fit_cutoff)
        row = good.rows[-1]
        if self.mode == "refit_as_of":
            bad = TrainingRow(**{**row.__dict__, "feature_as_of": good.fit_as_of})
        elif self.mode == "same_day":
            bad = TrainingRow(
                **{
                    **row.__dict__,
                    "et_date": fit_cutoff,
                    "start_time_utc": good.fit_as_of + timedelta(hours=16),
                    "feature_as_of": good.fit_as_of,
                    "available_at": good.fit_as_of,
                }
            )
        else:
            bad = TrainingRow(**{**row.__dict__, "et_date": fit_cutoff})
        return TrainingMatrix(
            good.feature_version, good.fit_cutoff, good.fit_as_of, (*good.rows[:-1], bad)
        )


@pytest.mark.parametrize("mode", ["refit_as_of", "future", "same_day"])
def test_runner_guard_rejects_training_rows_that_break_6_2(conn: Connection, mode: str) -> None:
    """Features recomputed at the refit date, or a game not before the cutoff -> error."""
    with pytest.raises(LeakageGuardError):
        run_walk_forward(
            conn,
            HomeRateLearner(),
            WalkForwardConfig(seasons=("2023-24", "2024-25")),
            training_source=BadSource(conn, mode),
            now=NOW,
        )


class BadPredictor(ConstantPredictor):
    """Returns an invalid prediction."""

    def __init__(self, mode: str) -> None:
        """mode: 'certain' (p = 1) or 'stranger' (unknown game id)."""
        self.mode = mode

    def predict(
        self, as_of: datetime, games: Sequence[TargetGame], context: PredictionContext
    ) -> Mapping[str, Prediction]:
        """One bad value."""
        if self.mode == "certain":
            return {games[0].game_id: Prediction(p_home=1.0)}
        return {"0029999999": Prediction(p_home=0.5)}


@pytest.mark.parametrize("mode", ["certain", "stranger"])
def test_invalid_predictor_output_is_an_error(conn: Connection, mode: str) -> None:
    """p_home must be inside (0, 1) and only asked-for games may be predicted."""
    with pytest.raises(PredictorOutputError):
        run(conn, BadPredictor(mode))


def test_config_rejects_impossible_settings() -> None:
    """No seasons, negative lag or a zero refit cadence are refused."""
    with pytest.raises(ValueError):
        WalkForwardConfig(seasons=())
    with pytest.raises(ValueError):
        WalkForwardConfig(seasons=DEV, availability_lag=timedelta(hours=-1))
    with pytest.raises(ValueError):
        WalkForwardConfig(seasons=DEV, refit_every_days=0)


def test_started_games_are_skipped_not_predicted(conn: Connection, league: League) -> None:
    """A game whose tip-off is before its date's 07:00 UTC as_of (01:00 ET = 05:00 UTC) is
    counted as started_before_as_of and never predicted (design 1-2 "start_time_utc > as_of")."""
    a, b, _, _ = league.teams
    raw = conn.execute(text("SELECT min(raw_response_id) FROM raw_api_responses")).scalar_one()
    early = extreme_game("0022400900", "2024-25", et(date(2024, 10, 23), 1, 0), a, b)
    insert_games(conn, [early], raw)
    insert_stats(conn, early, raw)
    result = run(conn, seasons=("2024-25",))
    assert result.skipped[SKIP_STARTED] == 1
    assert "0022400900" not in {p.game.game_id for p in result.predictions}


@pytest.mark.parametrize(
    ("cutoff", "as_of", "start", "ok"),
    [
        (None, datetime(2024, 1, 2, 7, tzinfo=UTC), datetime(2024, 1, 3, tzinfo=UTC), True),
        (
            datetime(2024, 1, 2, 7, tzinfo=UTC),
            datetime(2024, 1, 2, 7, tzinfo=UTC),
            datetime(2024, 1, 3, tzinfo=UTC),
            True,
        ),
        (
            datetime(2024, 1, 2, 8, tzinfo=UTC),
            datetime(2024, 1, 2, 7, tzinfo=UTC),
            datetime(2024, 1, 3, tzinfo=UTC),
            False,
        ),
        (None, datetime(2024, 1, 2, 7, tzinfo=UTC), datetime(2024, 1, 2, 7, tzinfo=UTC), False),
    ],
)
def test_prediction_time_guard(
    cutoff: datetime | None, as_of: datetime, start: datetime, ok: bool
) -> None:
    """train_cutoff <= as_of < tip-off, each side alone (T8 guard)."""
    if ok:
        _check_prediction_times("g", cutoff, as_of, start)
    else:
        with pytest.raises(LeakageGuardError, match="train_cutoff/as_of/tip-off order"):
            _check_prediction_times("g", cutoff, as_of, start)


class LyingSource:
    """Honest rows except one self-reported value that disagrees with the games table while
    every other training rule still holds (so only the cross-check can catch it)."""

    def __init__(self, conn: Connection, lie: str) -> None:
        """lie: 'available_at' (a day earlier), 'start_time_utc' (an hour earlier), 'et_date'
        (a day earlier, features as of that date), 'season' (another season label) or
        'game_id' (a game that is not in the games table)."""
        self.inner = ScheduleOnlyTrainingSource(load_games(conn), train_start_season="2023-24")
        self.lie = lie

    def training_matrix(self, feature_version: str, fit_cutoff: date) -> TrainingMatrix:
        """The honest matrix with the first row's self-report altered."""
        good = self.inner.training_matrix(feature_version, fit_cutoff)
        row = good.rows[0]
        day = timedelta(days=1)
        bad = {
            "available_at": replace(row, available_at=row.available_at - day),
            "start_time_utc": replace(row, start_time_utc=row.start_time_utc - timedelta(hours=1)),
            "et_date": replace(
                row, et_date=row.et_date - day, feature_as_of=backtest_as_of(row.et_date - day)
            ),
            "season": replace(row, season="2099-00"),
            "game_id": replace(row, game_id="0029999999"),
        }[self.lie]
        return TrainingMatrix(
            good.feature_version, good.fit_cutoff, good.fit_as_of, (bad, *good.rows[1:])
        )


@pytest.mark.parametrize("lie", ["available_at", "start_time_utc", "et_date", "season", "game_id"])
def test_runner_cross_checks_self_reported_training_values(conn: Connection, lie: str) -> None:
    """P-032 review (a): a training row's own et_date, start_time_utc, available_at and
    season must match the games table by game_id. Each lie passes every other rule (the
    guard without the games table accepts it) and the cross-check refuses it."""
    source = LyingSource(conn, lie)
    cutoff = date(2024, 11, 20)
    matrix = source.training_matrix("dummy-fv0", cutoff)
    _check_training(matrix, cutoff, backtest_as_of(cutoff))  # every other rule holds
    with pytest.raises(LeakageGuardError, match="self-reports|not a loaded game"):
        run_walk_forward(
            conn,
            HomeRateLearner(),
            WalkForwardConfig(seasons=("2023-24", "2024-25")),
            training_source=source,
            now=NOW,
        )


def older_season_games(league: League) -> list[dict]:
    """Five final 2022-23 games (A-B, January 2023) that are not in the fixture."""
    a, b, _, _ = league.teams
    return [
        extreme_game(f"00222{n:05d}", "2022-23", et(date(2023, 1, 10) + timedelta(2 * n), 20), a, b)
        for n in range(5)
    ]


def test_training_window_starts_at_the_first_requested_season(
    conn: Connection, league: League
) -> None:
    """P-032 review (b), M-6/M-11: with 2022-23 games in the database (P-026), a learner over
    2023-24 and 2024-25 trains on no 2022-23 game, predicts none and scores none, while a
    non-learning predictor still sees them as state inputs (Elo warm-up, 6-3 item 2); a
    source that hands the runner a 2022-23 row is a guard error."""
    raw = conn.execute(text("SELECT min(raw_response_id) FROM raw_api_responses")).scalar_one()
    older = older_season_games(league)
    insert_games(conn, older, raw)
    for game in older:
        insert_stats(conn, game, raw)
    older_ids = {g["game_id"] for g in older}
    learner = HomeRateLearner()
    result = run(conn, learner, seasons=("2023-24", "2024-25"))
    assert result.config.train_start_season == "2023-24"
    assert result.refits[0].n_rows == 174  # the whole of 2023-24 and nothing older
    assert not any(r.season < "2023-24" for fit in learner.fits for r in fit.rows)
    assert {p.game.season for p in result.predictions} == {"2024-25"}
    scoring = score_run(conn, result)
    assert not any(r.prediction.game.game_id in older_ids for r in scoring.records)
    probe = Probe()
    run(conn, probe, seasons=("2023-24",))
    assert older_ids <= set(probe.seen[min(probe.seen)])
    with pytest.raises(LeakageGuardError, match="before the training window"):
        run_walk_forward(
            conn,
            HomeRateLearner(),
            WalkForwardConfig(seasons=("2023-24", "2024-25")),
            training_source=ScheduleOnlyTrainingSource(load_games(conn)),
            now=NOW,
        )


class LateSource:
    """Training rows reported available after the prediction as_of."""

    def __init__(self, conn: Connection) -> None:
        """Wrap the honest source."""
        self.inner = ScheduleOnlyTrainingSource(load_games(conn), train_start_season="2023-24")

    def training_matrix(self, feature_version: str, fit_cutoff: date) -> TrainingMatrix:
        """Every row's available_at a day after the refit as_of."""
        good = self.inner.training_matrix(feature_version, fit_cutoff)
        late = good.fit_as_of + timedelta(days=1)
        rows = tuple(TrainingRow(**{**r.__dict__, "available_at": late}) for r in good.rows)
        return TrainingMatrix(good.feature_version, good.fit_cutoff, good.fit_as_of, rows)


def test_prediction_time_guard_is_wired_into_the_runner(
    conn: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the training guard switched off, the per-prediction guard alone still stops a
    train_cutoff after as_of."""
    monkeypatch.setattr(walkforward, "_check_training", lambda *args, **kwargs: None)
    with pytest.raises(LeakageGuardError, match="train_cutoff/as_of/tip-off order"):
        run_walk_forward(
            conn,
            HomeRateLearner(),
            WalkForwardConfig(seasons=("2023-24", "2024-25")),
            training_source=LateSource(conn),
            now=NOW,
        )


def change_odds_after(conn: Connection, moment: datetime) -> int:
    """Absurd prices and lines on every snapshot captured after `moment`."""
    return conn.execute(
        text(
            """
            UPDATE odds_snapshots
            SET price_decimal = CASE WHEN outcome_side IN ('home', 'over') THEN 1.01 ELSE 25 END,
                point = point + 30
            WHERE captured_at > :moment
            """
        ),
        {"moment": moment},
    ).rowcount


@pytest.mark.parametrize("name", [PROBE, "B3"])
def test_odds_time_travel_at_runner_level(conn: Connection, league: League, name: str) -> None:
    """Odds rows after the 2026-10-21 as_of are changed (the 10-21 closing quotes and the
    10-22 game's quote) and new quotes at as_of + 1 microsecond are added: predictions and
    market_reference up to 10-21 are identical (probe and market baseline B3). Power: the
    10-22 prediction changes, and a quote exactly at as_of (equality, T6) changes the 10-21
    prediction."""
    pivot = date(2026, 10, 21)
    as_of = backtest_as_of(pivot)
    before = run(conn, name, seasons=("2026-27",))
    game = league.games[LIVE_AVAILABLE]
    event = conn.execute(
        text("SELECT odds_event_id FROM odds_events WHERE game_id = :g"), {"g": LIVE_AVAILABLE}
    ).scalar_one()
    raw = conn.execute(text("SELECT min(raw_response_id) FROM raw_api_responses")).scalar_one()
    assert change_odds_after(conn, as_of) == 10  # 8 closing rows of 10-21 + 2 of 10-22
    later = as_of + timedelta(microseconds=1)
    for book in ("draftkings", "fanduel", "betmgm", "bovada"):
        for side, price in (("home", "30"), ("away", "1.02")):
            insert_snapshot(
                conn, event, game["start_time_utc"], later, book, "h2h", side, price, raw
            )
    after = run(conn, name, seasons=("2026-27",))
    assert outputs(after, pivot) == outputs(before, pivot)
    changed = [o for o in outputs(after) if o not in outputs(before)]
    assert [o[0] for o in changed] == [LIVE_PRED_ONLY]
    for side, price in (("home", "1.20"), ("away", "5.00")):
        insert_snapshot(
            conn, event, game["start_time_utc"], as_of, "draftkings", "h2h", side, price, raw
        )
    equal = run(conn, name, seasons=("2026-27",))
    assert outputs(equal, pivot) != outputs(before, pivot)
    if name == "B3":
        assert isinstance(make(name), MarketBaseline)
