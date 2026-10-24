"""Win baselines B0, B1 and B3 of modeling design v1.2 5-1 (P-033).

Each baseline is a non-learning predictor of the walk-forward runner
(presagio.evaluation.walkforward.Predictor, matched structurally: this package never imports
presagio.evaluation, design 9-3 T6). A baseline reads nothing but the PredictionContext the
runner hands it: results() (finished games passing R1 and R2), schedule() and the R3 markets.

- B0 always home:   p_home = the season's cumulative home win rate (F-17 league_home_rate_std)
                    once the season sample has at least MIN_SEASON_GAMES games, before that the
                    previous season's final home win rate, and 0.5 without one; 0.5 for neutral
                    games; clipped to WIN_RATE_CLIP like B1 (D-055 item 6: a 0/1 rate from a
                    tiny sample must not stop the run). "Always home" for accuracy is
                    p_home >= 0.5.
- B1 season win %:  p_home = 0.5 + 0.5 x (win%_home - win%_away), clipped to WIN_RATE_CLIP;
                    0.5 (home) when the rates tie or either team has no game this season.
- B3 market:        p_home = the prediction-time consensus market probability (design 4-3,
                    rule R3); a game without one is not predicted at all.

Season cumulative values count every predicted-type game of the season (play-in and playoffs
included, design 2-4); the Cup final never reaches results() (D-049 M-3).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from presagio.models.spec import BASELINE_HOME, BASELINE_SEASON_WR, MARKET, ModelSpec

# Design 5-1 proposal values (D-049): season sample below which B0 falls back, and the
# probability clipping of B1 (D-055 item 6: B0 uses the same range).
MIN_SEASON_GAMES = 100
WIN_RATE_CLIP = (0.05, 0.95)
NEUTRAL_P_HOME = 0.5
NO_SAMPLE_P_HOME = 0.5
TIE_P_HOME = 0.5


class Result(Protocol):
    """A finished game as the runner shows it (presagio.evaluation.walkforward.GameResult)."""

    game_id: str
    season: str
    home_team_id: int
    away_team_id: int
    is_neutral: bool

    @property
    def home_won(self) -> bool:
        """Home team won."""
        ...


class Target(Protocol):
    """A game to predict (presagio.evaluation.walkforward.TargetGame): schedule only."""

    game_id: str
    season: str
    home_team_id: int
    away_team_id: int
    is_neutral: bool


class MarketLike(Protocol):
    """A prediction-time market (presagio.evaluation.market.GameMarket)."""

    @property
    def home(self) -> Any:
        """MarketProbability with p_home, or None."""
        ...


class Context(Protocol):
    """What a baseline reads (presagio.evaluation.walkforward.PredictionContext)."""

    def results(self) -> Sequence[Result]:
        """Finished games passing R1 and R2 for the day, in start order."""
        ...

    def market(self, game_id: str) -> MarketLike | None:
        """Prediction-time market of a target game (R3), or None."""
        ...


@dataclass(frozen=True)
class BaselinePrediction:
    """What a win baseline returns per game: p_home only (no total, no over probability)."""

    p_home: float
    total_mean: float | None = None
    over_prob: float | None = None
    total_sd: float | None = None


@dataclass(frozen=True)
class SeasonRecord:
    """A team's season-to-date record from the visible results."""

    games: int
    wins: int

    @property
    def win_rate(self) -> float | None:
        """wins / games, None before the first game (design 2-4: NULL at game 0)."""
        return None if self.games == 0 else self.wins / self.games


def season_results(results: Iterable[Result], season: str) -> list[Result]:
    """The visible results of one season."""
    return [r for r in results if r.season == season]


def home_win_rate(results: Sequence[Result]) -> float | None:
    """Share of games the home team won, None without games."""
    return None if not results else sum(r.home_won for r in results) / len(results)


def previous_season(results: Iterable[Result], season: str) -> str | None:
    """The latest visible season before `season` (season labels sort as strings), or None."""
    earlier = {r.season for r in results if r.season < season}
    return max(earlier) if earlier else None


def validate_clip(clip: tuple[float, float]) -> tuple[float, float]:
    """A clipping range with 0 < lower <= 0.5 <= upper < 1, as floats; ValueError otherwise."""
    lower, upper = clip
    if not 0.0 < lower <= 0.5 <= upper < 1.0:
        raise ValueError(f"clip must satisfy 0 < lower <= 0.5 <= upper < 1, got {clip!r}")
    return (float(lower), float(upper))


def clip_probability(p: float, clip: tuple[float, float] = WIN_RATE_CLIP) -> float:
    """p clipped into [lower, upper]."""
    lower, upper = clip
    return min(max(p, lower), upper)


def b0_home_probability(
    results: Sequence[Result],
    game: Target,
    *,
    min_season_games: int = MIN_SEASON_GAMES,
    clip: tuple[float, float] = WIN_RATE_CLIP,
) -> float:
    """B0: season home win rate, previous season's before the sample is big enough, else 0.5;
    0.5 for a neutral game (design 5-1, F-17); the rate is clipped (D-055 item 6)."""
    if game.is_neutral:
        return NEUTRAL_P_HOME
    this_season = season_results(results, game.season)
    if len(this_season) >= min_season_games:
        rate = home_win_rate(this_season)
        assert rate is not None
        return clip_probability(rate, clip)
    previous = previous_season(results, game.season)
    if previous is None:
        return NO_SAMPLE_P_HOME
    rate = home_win_rate(season_results(results, previous))
    return NO_SAMPLE_P_HOME if rate is None else clip_probability(rate, clip)


def season_records(results: Iterable[Result], season: str) -> dict[int, SeasonRecord]:
    """Season-to-date (games, wins) per team from the visible results of `season`."""
    games: dict[int, int] = {}
    wins: dict[int, int] = {}
    for r in results:
        if r.season != season:
            continue
        for team, won in ((r.home_team_id, r.home_won), (r.away_team_id, not r.home_won)):
            games[team] = games.get(team, 0) + 1
            wins[team] = wins.get(team, 0) + int(won)
    return {team: SeasonRecord(games[team], wins[team]) for team in games}


def b1_home_probability(
    home_rate: float | None,
    away_rate: float | None,
    *,
    clip: tuple[float, float] = WIN_RATE_CLIP,
) -> float:
    """B1: 0.5 + 0.5 x (win%_h - win%_a) clipped; 0.5 when either side has no game or on a
    tie (design 5-1)."""
    if home_rate is None or away_rate is None or home_rate == away_rate:
        return TIE_P_HOME
    return clip_probability(0.5 + 0.5 * (home_rate - away_rate), clip)


class AlwaysHomeBaseline:
    """B0: p_home = cumulative home win rate (season, previous season, or 0.5); neutral 0.5."""

    name = "B0 always home (season home win rate)"
    trains = False
    feature_version = None
    uses_results = True

    def __init__(
        self, min_season_games: int = MIN_SEASON_GAMES, clip: tuple[float, float] = WIN_RATE_CLIP
    ) -> None:
        """Keep the fallback threshold and the clipping range (proposal values, 5-1, D-055)."""
        if min_season_games < 1:
            raise ValueError("min_season_games must be >= 1")
        self.min_season_games = min_season_games
        self.clip = validate_clip(clip)

    @property
    def spec(self) -> ModelSpec:
        """model_versions identity of this configuration."""
        return ModelSpec(
            model_name="always_home_b0",
            version="1",
            model_family=BASELINE_HOME,
            hyperparameters={
                "min_season_games": self.min_season_games,
                "clip": list(self.clip),
                "neutral_p_home": NEUTRAL_P_HOME,
                "no_sample_p_home": NO_SAMPLE_P_HOME,
                "rate": "season cumulative home win rate (F-17), previous season before "
                "min_season_games, clipped",
            },
        )

    def fit(self, training: Any) -> None:
        """Nothing to learn."""

    def predict(
        self, as_of: datetime, games: Sequence[Target], context: Context
    ) -> Mapping[str, BaselinePrediction]:
        """Every target game gets the B0 probability."""
        results = tuple(context.results())
        return {
            g.game_id: BaselinePrediction(
                b0_home_probability(
                    results, g, min_season_games=self.min_season_games, clip=self.clip
                )
            )
            for g in games
        }


class SeasonWinRateBaseline:
    """B1: the team with the higher season win rate; probability from the rate difference."""

    name = "B1 season win rate (0.5 + 0.5 x diff, clipped)"
    trains = False
    feature_version = None
    uses_results = True

    def __init__(self, clip: tuple[float, float] = WIN_RATE_CLIP) -> None:
        """Keep the clipping range (a proposal value, design 5-1)."""
        self.clip = validate_clip(clip)

    @property
    def spec(self) -> ModelSpec:
        """model_versions identity of this configuration."""
        return ModelSpec(
            model_name="season_win_rate_b1",
            version="1",
            model_family=BASELINE_SEASON_WR,
            hyperparameters={
                "formula": "0.5 + 0.5 * (win_rate_home - win_rate_away)",
                "clip": list(self.clip),
                "tie_or_first_game_p_home": TIE_P_HOME,
                "season_cumulative": "regular + play-in + playoffs (design 2-4)",
            },
        )

    def fit(self, training: Any) -> None:
        """Nothing to learn."""

    def predict(
        self, as_of: datetime, games: Sequence[Target], context: Context
    ) -> Mapping[str, BaselinePrediction]:
        """Season records per team, then the B1 formula per game."""
        results = tuple(context.results())
        out: dict[str, BaselinePrediction] = {}
        records_by_season: dict[str, dict[int, SeasonRecord]] = {}
        for g in games:
            if g.season not in records_by_season:
                records_by_season[g.season] = season_records(results, g.season)
            records = records_by_season[g.season]
            home = records.get(g.home_team_id, SeasonRecord(0, 0)).win_rate
            away = records.get(g.away_team_id, SeasonRecord(0, 0)).win_rate
            out[g.game_id] = BaselinePrediction(b1_home_probability(home, away, clip=self.clip))
        return out


class MarketBaseline:
    """B3: the prediction-time market probability (design 4-3); no market, no prediction."""

    name = "B3 market (prediction-time consensus, R3)"
    trains = False
    feature_version = None
    # Reads no past result: train_cutoff of its predictions is as_of (schema v1 9-4).
    uses_results = False

    @property
    def spec(self) -> ModelSpec:
        """model_versions identity of this configuration."""
        return ModelSpec(
            model_name="market_b3",
            version="1",
            model_family=MARKET,
            hyperparameters={
                "probability": "prediction-time margin-free home probability (design 4-3): "
                "consensus median, else the reference bookmaker; market rules are the "
                "runner's (model_versions.training_window)",
            },
        )

    def fit(self, training: Any) -> None:
        """Nothing to learn."""

    def predict(
        self, as_of: datetime, games: Sequence[Target], context: Context
    ) -> Mapping[str, BaselinePrediction]:
        """Only games with a prediction-time home probability are predicted."""
        out: dict[str, BaselinePrediction] = {}
        for g in games:
            market = context.market(g.game_id)
            if market is None or market.home is None:
                continue
            out[g.game_id] = BaselinePrediction(float(market.home.p_home))
        return out
