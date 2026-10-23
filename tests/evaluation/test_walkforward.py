"""The walk-forward runner on the temp league DB: time rules and leakage tests at runner level
(design 9-3 T1, T2, T4, T8, T10, T12, T14 interface part)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Connection, text

from presagio.evaluation.data import load_games, load_prediction_quotes
from presagio.evaluation.dummy import ConstantPredictor, HomeRateLearner
from presagio.evaluation.holdout import HoldoutLockedError
from presagio.evaluation.market import prediction_market
from presagio.evaluation.timing import backtest_as_of, et_date
from presagio.evaluation.walkforward import (
    SKIP_AFTER_NOW,
    SKIP_MIN_TRAINING,
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
    run_walk_forward,
)

from .league import (
    CUP_FINAL,
    LIVE_AVAILABLE,
    LIVE_NOT_FINAL,
    LIVE_PRED_ONLY,
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


def run(conn: Connection, predictor: Any = None, **config: Any) -> WalkForwardResult:
    """run_walk_forward with defaults for the tests."""
    seasons = config.pop("seasons", DEV)
    now = config.pop("now", NOW)
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


def test_t1_time_travel_changes_after_as_of_leave_predictions_unchanged(
    conn: Connection, league: League
) -> None:
    """T1 at runner level. After predicting, (i) add ten extreme games after the pivot date,
    (ii) change the pivot games' own scores and stats (the target g and the same-date other
    game). Predictions up to the pivot date stay identical; later ones change (the probe is
    sensitive, so the test has power)."""
    before = run(conn)
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
    after = run(conn)
    assert outputs(after, PIVOT) == outputs(before, PIVOT)
    assert outputs(after) != outputs(before)


def test_t2_future_injection_leaves_past_predictions_unchanged(
    conn: Connection, league: League
) -> None:
    """T2 at runner level: every game from the pivot date on (the target included) becomes
    300-0 with absurd stats; predictions up to the pivot date are identical."""
    before = run(conn)
    changed = make_extreme(conn, "status_code = 3 AND start_time_utc >= :lo", {"lo": et(PIVOT, 0)})
    assert changed > 50
    after = run(conn)
    assert outputs(after, PIVOT) == outputs(before, PIVOT)
    later = [o for o in outputs(after) if o not in outputs(before)]
    assert later and all(league.games[o[0]]["start_time_utc"] > et(PIVOT, 23, 59) for o in later)


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


def test_t12_same_run_twice_gives_identical_predictions(conn: Connection) -> None:
    """T12 (in memory until P-033 stores predictions): two runs, identical values."""
    assert outputs(run(conn)) == outputs(run(conn))


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
        """mode: 'refit_as_of' (features at the refit date) or 'future' (D(h) >= cutoff)."""
        self.inner = ScheduleOnlyTrainingSource(load_games(conn))
        self.mode = mode

    def training_matrix(self, feature_version: str, fit_cutoff: date) -> TrainingMatrix:
        """The honest matrix with one row corrupted."""
        good = self.inner.training_matrix(feature_version, fit_cutoff)
        row = good.rows[-1]
        if self.mode == "refit_as_of":
            bad = TrainingRow(**{**row.__dict__, "feature_as_of": good.fit_as_of})
        else:
            bad = TrainingRow(**{**row.__dict__, "et_date": fit_cutoff})
        return TrainingMatrix(
            good.feature_version, good.fit_cutoff, good.fit_as_of, (*good.rows[:-1], bad)
        )


@pytest.mark.parametrize("mode", ["refit_as_of", "future"])
def test_runner_guard_rejects_training_rows_that_break_6_2(conn: Connection, mode: str) -> None:
    """Features recomputed at the refit date, or a game not before the cutoff -> error."""
    with pytest.raises(LeakageGuardError):
        run_walk_forward(
            conn,
            HomeRateLearner(),
            WalkForwardConfig(seasons=("2024-25",)),
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
