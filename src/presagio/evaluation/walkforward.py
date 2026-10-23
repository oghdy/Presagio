"""Walk-forward backtest runner (modeling design v1.2 6-2, 2-2, 2-3, 6-3, 6-4).

One iteration per US Eastern date d with target games, in date order. as_of = d 07:00 UTC.
A predictor sees only what the runner puts in front of it (the runner's game list, with
today's and future scores, never leaves the runner):

- the target games' schedule (TargetGame has no score or status),
- a frozen PredictionContext built before predict() is called, holding
  results(): finished games passing R1 and R2 for (d, as_of),
  schedule(): score-free schedule of earlier dates (Cup final included, for rest features),
  market(game_id): the prediction-time market by rule R3,
- for learning predictors, fit(TrainingMatrix) at refit days. The runner asks the training
  source for training_matrix(feature_version, fit_cutoff) only: a training row h carries the
  features of h's own prediction date (as_of(h)), never features recomputed at the refit
  date, and the predictor gets no as_of to pass to a feature function (6-2, T14).

Learning predictors refit every 7 days (expanding window from the first season) and start
predicting after the minimum-training season (2023-24, D-049 M-6). Non-learning baselines
predict from the first season but are reported only from 2024-01-01 (warm-up). Holdout
seasons are refused without an unlock reason (6-3). Nothing is stored (P-033 adds storage).
"""

from __future__ import annotations

import hashlib
import logging
import math
from bisect import bisect_left
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from sqlalchemy import Connection

from presagio.evaluation.clv import CLOSING_TOLERANCE
from presagio.evaluation.data import GameRecord, load_games, load_prediction_quotes
from presagio.evaluation.holdout import HOLDOUT_SEASONS, HoldoutDecision, check_holdout
from presagio.evaluation.market import (
    CONSENSUS_MIN_BOOKS,
    DEFAULT_MARGIN_METHOD,
    MAX_MARKET_AGE,
    REFERENCE_BOOKMAKERS,
    GameMarket,
    prediction_market,
)
from presagio.evaluation.timing import (
    DEFAULT_AVAILABILITY_LAG,
    available_at,
    backtest_as_of,
    is_available,
)

logger = logging.getLogger("presagio.evaluation.walkforward")

# D-028 A-4 and D-049 M-3: predicted and scored game types (cup_final never).
PREDICTED_GAME_TYPES = ("regular", "playin", "playoffs")
POSTSEASON_TYPES = ("playin", "playoffs")
# D-049 M-6 (proposal values): refit cadence, minimum training, baseline warm-up.
DEFAULT_REFIT_EVERY_DAYS = 7
MIN_TRAIN_SEASON = "2023-24"
BASELINE_REPORT_FROM = date(2024, 1, 1)
SNAPSHOT_KIND = "daily"
ORIGIN = "backtest"

# Reasons a target game got no reported prediction (the report prints the counts).
SKIP_AFTER_NOW = "as_of_in_future"
SKIP_STARTED = "started_before_as_of"
SKIP_MIN_TRAINING = "min_training"
SKIP_NOT_PREDICTED = "not_predicted"
SKIP_TBD = "teams_or_time_tbd"


class LeakageGuardError(RuntimeError):
    """The runner caught a time-rule violation (a bug, never data to keep)."""


class PredictorOutputError(ValueError):
    """A predictor returned an invalid or unexpected prediction."""


@dataclass(frozen=True)
class TargetGame:
    """What a predictor may know about a game it predicts: schedule only, no result."""

    game_id: str
    season: str
    game_type: str
    et_date: date
    start_time_utc: datetime
    home_team_id: int
    away_team_id: int
    is_neutral: bool


@dataclass(frozen=True)
class GameResult:
    """A finished game a predictor may use (it passed R1 and R2 for the current day)."""

    game_id: str
    season: str
    game_type: str
    et_date: date
    start_time_utc: datetime
    available_at: datetime
    home_team_id: int
    away_team_id: int
    home_score: int
    away_score: int
    is_neutral: bool

    @property
    def home_won(self) -> bool:
        """Home team won (no ties in the NBA)."""
        return self.home_score > self.away_score

    @property
    def total(self) -> int:
        """Total points."""
        return self.home_score + self.away_score


@runtime_checkable
class PredictionValues(Protocol):
    """What predict() returns per game; any object with these attributes works."""

    @property
    def p_home(self) -> float:
        """Home win probability in (0, 1)."""
        ...

    @property
    def total_mean(self) -> float | None:
        """Expected total points."""
        ...

    @property
    def over_prob(self) -> float | None:
        """P(total > reference line), when the predictor has a line."""
        ...


@dataclass(frozen=True)
class Prediction:
    """A plain PredictionValues for predictors that do not bring their own type."""

    p_home: float
    total_mean: float | None = None
    over_prob: float | None = None
    total_sd: float | None = None


@dataclass(frozen=True)
class ScheduledGame:
    """A game on the schedule before the target date: schedule only, no score, no status.

    For schedule features (rest days, back-to-backs); it includes the Cup final, which
    results() leaves out (D-049 M-3). Only dates before the target date are listed: the
    final schedule of later dates can reveal results (if-necessary playoff games).
    """

    game_id: str
    season: str
    game_type: str
    et_date: date
    start_time_utc: datetime
    home_team_id: int
    away_team_id: int
    is_neutral: bool


@dataclass(frozen=True)
class PredictionContext:
    """Everything a predictor may read on one day, built by the runner beforehand.

    It holds the R1/R2 results, the schedule before the target date and the R3 markets of
    the target games, and nothing else: no game list, no score outside results(), no
    closing odds. Frozen, so a predictor cannot move as_of or target_date.
    """

    as_of: datetime
    target_date: date
    lag: timedelta
    visible_results: tuple[GameResult, ...]
    past_schedule: tuple[ScheduledGame, ...]
    markets: Mapping[str, GameMarket]

    def results(self) -> tuple[GameResult, ...]:
        """Finished predicted-type games with D(h) < target date, start + lag <= as_of and
        both stat rows, in start order (cup_final excluded, D-049 M-3)."""
        return self.visible_results

    def schedule(self) -> tuple[ScheduledGame, ...]:
        """Score-free schedule of every loaded game with D(h) < target date, cup_final
        included, in start order."""
        return self.past_schedule

    def market(self, game_id: str) -> GameMarket | None:
        """Prediction-time market of a target game (R3), or None."""
        return self.markets.get(game_id)

    @property
    def info_cutoff(self) -> datetime | None:
        """Latest available_at among the visible results (None before any result)."""
        results = self.visible_results
        return max(r.available_at for r in results) if results else None


class _History:
    """The runner's index of the loaded games. It stays inside the runner: a predictor only
    ever gets the PredictionContext it builds for one day."""

    def __init__(self, games: Sequence[GameRecord], lag: timedelta) -> None:
        """Index games sorted by start (so D(h) < d is a prefix)."""
        self._games = list(games)
        self._lag = lag
        self._dates = [g.et_date for g in self._games]
        if self._dates != sorted(self._dates):
            raise ValueError("games must be in start order")
        self._results = [self._as_result(g) for g in self._games]
        self._schedule = [
            None
            if g.home_team_id is None or g.away_team_id is None
            else ScheduledGame(
                game_id=g.game_id,
                season=g.season,
                game_type=g.game_type,
                et_date=g.et_date,
                start_time_utc=g.start_time_utc,
                home_team_id=g.home_team_id,
                away_team_id=g.away_team_id,
                is_neutral=g.is_neutral,
            )
            for g in self._games
        ]

    def _as_result(self, g: GameRecord) -> GameResult | None:
        """A GameResult candidate (predicted type, teams and score known), else None."""
        if (
            g.game_type not in PREDICTED_GAME_TYPES
            or g.home_team_id is None
            or g.away_team_id is None
            or g.home_score is None
            or g.away_score is None
        ):
            return None
        return GameResult(
            game_id=g.game_id,
            season=g.season,
            game_type=g.game_type,
            et_date=g.et_date,
            start_time_utc=g.start_time_utc,
            available_at=available_at(g.start_time_utc, self._lag),
            home_team_id=g.home_team_id,
            away_team_id=g.away_team_id,
            home_score=g.home_score,
            away_score=g.away_score,
            is_neutral=g.is_neutral,
        )

    def context(
        self, day: date, as_of: datetime, markets: Mapping[str, GameMarket]
    ) -> PredictionContext:
        """The context of ET date `day` at as_of: R1 prefix, then R2 per game."""
        end = bisect_left(self._dates, day)
        results = tuple(
            r
            for g, r in zip(self._games[:end], self._results[:end], strict=True)
            if r is not None
            and is_available(
                start_time_utc=g.start_time_utc,
                status_code=g.status_code,
                has_stats=g.has_stats,
                target_date=day,
                as_of=as_of,
                lag=self._lag,
                game_et_date=g.et_date,
            )
        )
        schedule = tuple(s for s in self._schedule[:end] if s is not None)
        return PredictionContext(
            as_of=as_of,
            target_date=day,
            lag=self._lag,
            visible_results=results,
            past_schedule=schedule,
            markets=MappingProxyType(dict(markets)),
        )


@dataclass(frozen=True)
class TrainingRow:
    """One training game h: features as of h's own prediction date, and its labels."""

    game_id: str
    season: str
    et_date: date
    start_time_utc: datetime
    feature_as_of: datetime  # as_of(h) = backtest_as_of(D(h)); never the refit date
    available_at: datetime
    features: Mapping[str, float | None]
    home_won: bool
    total: int


@dataclass(frozen=True)
class TrainingMatrix:
    """Training set of one refit: games with D(h) < fit_cutoff that passed R2 at its as_of."""

    feature_version: str
    fit_cutoff: date
    fit_as_of: datetime
    rows: tuple[TrainingRow, ...]

    @property
    def max_available_at(self) -> datetime | None:
        """train_cutoff: latest available_at of the training games (<= fit_as_of)."""
        return max((r.available_at for r in self.rows), default=None)

    @property
    def fingerprint(self) -> str:
        """sha256 over the sorted training game ids (logged at every refit, T8)."""
        joined = ",".join(sorted(r.game_id for r in self.rows))
        return hashlib.sha256(joined.encode()).hexdigest()[:16]


class TrainingMatrixSource(Protocol):
    """Where training rows come from; the only door a learning predictor's data uses."""

    def training_matrix(self, feature_version: str, fit_cutoff: date) -> TrainingMatrix:
        """Rows (h, features at as_of(h), labels) for D(h) < fit_cutoff (6-2)."""
        ...


class ScheduleOnlyTrainingSource:
    """Dummy training source until stored features exist (P-035/P-033 replace it).

    Features are schedule values only ({"home_adv": 0 or 1}); the real source reads stored
    `features` rows (h, as_of(h), feature_version), preferring origin = live over backtest.
    """

    def __init__(
        self, games: Sequence[GameRecord], lag: timedelta = DEFAULT_AVAILABILITY_LAG
    ) -> None:
        """Hold the loaded games and the availability lag."""
        self._games = games
        self._lag = lag

    def training_matrix(self, feature_version: str, fit_cutoff: date) -> TrainingMatrix:
        """Finished games with D(h) < fit_cutoff passing R2 at fit_cutoff's as_of."""
        fit_as_of = backtest_as_of(fit_cutoff)
        rows = tuple(
            TrainingRow(
                game_id=g.game_id,
                season=g.season,
                et_date=g.et_date,
                start_time_utc=g.start_time_utc,
                feature_as_of=backtest_as_of(g.et_date),
                available_at=available_at(g.start_time_utc, self._lag),
                features={"home_adv": 0.0 if g.is_neutral else 1.0},
                home_won=g.home_score > g.away_score,
                total=g.home_score + g.away_score,
            )
            for g in self._games
            if g.game_type in PREDICTED_GAME_TYPES
            and g.home_score is not None
            and g.away_score is not None
            and is_available(
                start_time_utc=g.start_time_utc,
                status_code=g.status_code,
                has_stats=g.has_stats,
                target_date=fit_cutoff,
                as_of=fit_as_of,
                lag=self._lag,
                game_et_date=g.et_date,
            )
        )
        return TrainingMatrix(feature_version, fit_cutoff, fit_as_of, rows)


class Predictor(Protocol):
    """A model or baseline plugged into the runner (B0..B3 and later models, P-033+)."""

    name: str
    trains: bool  # True: refit on the cadence, minimum-training rule applies
    feature_version: str | None

    def fit(self, training: TrainingMatrix) -> None:
        """Refit on stored-feature training rows (learning predictors only)."""
        ...

    def predict(
        self, as_of: datetime, games: Sequence[TargetGame], context: PredictionContext
    ) -> Mapping[str, PredictionValues]:
        """Predictions keyed by game_id for (some of) the day's target games."""
        ...


@dataclass(frozen=True)
class WalkForwardConfig:
    """Runner settings; defaults are the D-049 values (all of them proposals)."""

    seasons: tuple[str, ...]
    availability_lag: timedelta = DEFAULT_AVAILABILITY_LAG
    refit_every_days: int = DEFAULT_REFIT_EVERY_DAYS
    max_market_age: timedelta = MAX_MARKET_AGE
    closing_tolerance: timedelta = CLOSING_TOLERANCE  # used when scoring, after the run
    margin_method: str = DEFAULT_MARGIN_METHOD
    consensus_min_books: int = CONSENSUS_MIN_BOOKS
    reference_bookmakers: tuple[str, ...] = REFERENCE_BOOKMAKERS
    min_train_season: str = MIN_TRAIN_SEASON
    baseline_report_from: date = BASELINE_REPORT_FROM
    unlock_holdout: str | None = None
    holdout_seasons: tuple[str, ...] = HOLDOUT_SEASONS

    def __post_init__(self) -> None:
        """Reject settings that would break the time rules."""
        if not self.seasons:
            raise ValueError("no seasons requested")
        if self.availability_lag < timedelta(0):
            raise ValueError("availability lag must be >= 0")
        if self.refit_every_days < 1:
            raise ValueError("refit cadence must be >= 1 day")


@dataclass(frozen=True)
class PredictionRecord:
    """One prediction the runner made (what P-033 stores in predictions)."""

    game: TargetGame
    as_of: datetime
    p_home: float
    total_mean: float | None
    over_prob: float | None
    total_sd: float | None
    reference_line: float | None  # L at as_of (R3), what the over probability is scored on
    reference_bookmaker: str | None
    train_cutoff: datetime | None
    market: GameMarket  # prediction-time market (R3)
    reported: bool  # False for the non-learning warm-up before baseline_report_from
    snapshot_kind: str = SNAPSHOT_KIND
    origin: str = ORIGIN


@dataclass(frozen=True)
class RefitRecord:
    """One refit of a learning predictor: the training-set fingerprint (T8)."""

    et_date: date
    fit_cutoff: date
    fit_as_of: datetime
    n_rows: int
    max_available_at: datetime | None
    fingerprint: str


@dataclass
class WalkForwardResult:
    """Everything one run produced (in memory)."""

    predictor_name: str
    config: WalkForwardConfig
    holdout: HoldoutDecision
    games: dict[str, GameRecord]
    predictions: list[PredictionRecord] = field(default_factory=list)
    refits: list[RefitRecord] = field(default_factory=list)
    skipped: Counter[str] = field(default_factory=Counter)
    days: int = 0


def target_games(
    games: Sequence[GameRecord], seasons: Sequence[str]
) -> dict[date, list[GameRecord]]:
    """Predicted-type games of the requested seasons grouped by ET date (1-2 filter)."""
    by_day: dict[date, list[GameRecord]] = {}
    for g in games:
        if g.season in seasons and g.game_type in PREDICTED_GAME_TYPES:
            by_day.setdefault(g.et_date, []).append(g)
    return dict(sorted(by_day.items()))


def _as_target(g: GameRecord) -> TargetGame:
    """The schedule-only view of a game (no score, no status)."""
    assert g.home_team_id is not None and g.away_team_id is not None
    return TargetGame(
        game_id=g.game_id,
        season=g.season,
        game_type=g.game_type,
        et_date=g.et_date,
        start_time_utc=g.start_time_utc,
        home_team_id=g.home_team_id,
        away_team_id=g.away_team_id,
        is_neutral=g.is_neutral,
    )


def _check_probability(value: float | None, what: str, game_id: str) -> float | None:
    """A probability strictly inside (0, 1), or None; PredictorOutputError otherwise."""
    if value is None:
        return None
    value = float(value)
    if not 0.0 < value < 1.0:
        raise PredictorOutputError(f"{what} for {game_id} must be in (0, 1), got {value!r}")
    return value


def _check_total(value: float | None, what: str, game_id: str) -> float | None:
    """A finite positive total, or None."""
    if value is None:
        return None
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise PredictorOutputError(f"{what} for {game_id} must be finite and > 0, got {value!r}")
    return value


def _check_training(matrix: TrainingMatrix, fit_cutoff: date, as_of: datetime) -> None:
    """Runner-level guard of the training rules (6-2): raise on any violation."""
    if matrix.fit_cutoff != fit_cutoff or matrix.fit_as_of != backtest_as_of(fit_cutoff):
        raise LeakageGuardError(f"training matrix is for {matrix.fit_cutoff}, not {fit_cutoff}")
    for row in matrix.rows:
        if not row.et_date < fit_cutoff:
            raise LeakageGuardError(f"training game {row.game_id} is not before {fit_cutoff}")
        if row.available_at > matrix.fit_as_of:
            raise LeakageGuardError(f"training game {row.game_id} not available at the refit")
        if row.feature_as_of != backtest_as_of(row.et_date):
            raise LeakageGuardError(
                f"training game {row.game_id} uses features as of {row.feature_as_of}, "
                f"not its own prediction date"
            )
        if not row.feature_as_of < row.start_time_utc:
            raise LeakageGuardError(f"training game {row.game_id} features are after tip-off")
    if matrix.max_available_at is not None and matrix.max_available_at > as_of:
        raise LeakageGuardError("training set reaches past the prediction as_of")


def _check_prediction_times(
    game_id: str, train_cutoff: datetime | None, as_of: datetime, start: datetime
) -> None:
    """Runner-level guard of every prediction (T8): train_cutoff <= as_of < tip-off."""
    if not as_of < start or (train_cutoff is not None and not train_cutoff <= as_of):
        raise LeakageGuardError(f"{game_id}: train_cutoff/as_of/tip-off order")


def run_walk_forward(
    conn: Connection,
    predictor: Predictor,
    config: WalkForwardConfig,
    *,
    training_source: TrainingMatrixSource | None = None,
    now: datetime | None = None,
    games: Sequence[GameRecord] | None = None,
) -> WalkForwardResult:
    """Predict every target game of the requested seasons day by day (6-2). Read only."""
    holdout = check_holdout(config.seasons, config.unlock_holdout, config.holdout_seasons)
    loaded = sorted(
        games if games is not None else load_games(conn),
        key=lambda g: (g.start_time_utc, g.game_id),
    )
    history = _History(loaded, config.availability_lag)
    if training_source is None:
        training_source = ScheduleOnlyTrainingSource(loaded, config.availability_lag)
    result = WalkForwardResult(
        predictor_name=predictor.name,
        config=config,
        holdout=holdout,
        games={g.game_id: g for g in loaded},
    )
    last_refit: date | None = None
    train_cutoff: datetime | None = None
    for day, day_games in target_games(loaded, config.seasons).items():
        as_of = backtest_as_of(day)
        if now is not None and as_of > now:
            result.skipped[SKIP_AFTER_NOW] += len(day_games)
            continue
        eligible: list[GameRecord] = []
        for g in day_games:
            if g.home_team_id is None or g.away_team_id is None or g.is_time_tbd:
                result.skipped[SKIP_TBD] += 1
            elif not g.start_time_utc > as_of:
                result.skipped[SKIP_STARTED] += 1
            elif predictor.trains and g.season <= config.min_train_season:
                result.skipped[SKIP_MIN_TRAINING] += 1
            else:
                eligible.append(g)
        if not eligible:
            continue
        result.days += 1
        if predictor.trains and (
            last_refit is None or (day - last_refit).days >= config.refit_every_days
        ):
            matrix = training_source.training_matrix(predictor.feature_version or "", day)
            _check_training(matrix, day, as_of)
            predictor.fit(matrix)
            last_refit, train_cutoff = day, matrix.max_available_at
            result.refits.append(
                RefitRecord(
                    day, day, matrix.fit_as_of, len(matrix.rows), train_cutoff, matrix.fingerprint
                )
            )
            logger.info(
                "refit %s: %d training games, max available_at %s, fingerprint %s",
                day,
                len(matrix.rows),
                train_cutoff,
                matrix.fingerprint,
            )
        ids = [g.game_id for g in eligible]
        quotes = load_prediction_quotes(conn, ids, as_of, config.max_market_age)
        markets = {
            gid: prediction_market(
                quotes.get(gid, []),
                as_of,
                method=config.margin_method,
                min_books=config.consensus_min_books,
                order=config.reference_bookmakers,
                max_age=config.max_market_age,
            )
            for gid in ids
        }
        context = history.context(day, as_of, markets)
        targets = [_as_target(g) for g in eligible]
        output = predictor.predict(as_of, targets, context)
        unknown = set(output) - set(ids)
        if unknown:
            raise PredictorOutputError(f"predictions for games not asked for: {sorted(unknown)}")
        cutoff = train_cutoff if predictor.trains else context.info_cutoff
        for target in targets:
            values = output.get(target.game_id)
            if values is None:
                result.skipped[SKIP_NOT_PREDICTED] += 1
                continue
            _check_prediction_times(target.game_id, cutoff, as_of, target.start_time_utc)
            market = markets[target.game_id]
            totals_ref = market.totals_reference
            p_home = _check_probability(values.p_home, "p_home", target.game_id)
            if p_home is None:
                raise PredictorOutputError(f"p_home for {target.game_id} is missing")
            result.predictions.append(
                PredictionRecord(
                    game=target,
                    as_of=as_of,
                    p_home=p_home,
                    total_mean=_check_total(values.total_mean, "total_mean", target.game_id),
                    over_prob=_check_probability(values.over_prob, "over_prob", target.game_id),
                    total_sd=_check_total(
                        getattr(values, "total_sd", None), "total_sd", target.game_id
                    ),
                    reference_line=market.total_line,
                    reference_bookmaker=totals_ref.bookmaker_key if totals_ref else None,
                    train_cutoff=cutoff,
                    market=market,
                    reported=predictor.trains or day >= config.baseline_report_from,
                )
            )
    return result
