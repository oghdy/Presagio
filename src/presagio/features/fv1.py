"""fv1 feature values as pure functions of what was visible before a game (design 3-1).

No database here: presagio.features.compute loads the prior games under rules R1 and R2 and
hands them in as records; this module turns them into the value dict whose keys are the
manifest's (presagio.features.manifest, the single source of the key list). Every value is
"something that could be known before the target game started": a PriorGame carries a score
and stat lines only when it passed R2 at the caller's as_of, and the pure functions never
look at anything else.

Rules and formulas (design 2-2, 2-4, 3-0, 3-1; D-035; D-049 M-3, M-7; Oliver 2004):

- Possessions per team-game: poss = FGA - OREB + TOV + 0.44 x FTA (D-035, Oliver 2004),
  game possessions = the mean of both teams' estimates (presagio.ingestion.possessions).
- Sum-based rates over a window of k games (design 3-0; never a mean of per-game rates):
    ortg   = 100 x sum(pts) / sum(game_poss)        F-01
    drtg   = 100 x sum(opp_pts) / sum(game_poss)    F-02 (opponent row's pts)
    net    = ortg - drtg                            F-03
    pace   = sum(game_poss) / sum(minutes / 240)    F-04 (overtime: minutes 265 ... 290)
    margin = sum(pts - opp_pts) / k                 F-05
    efg    = (sum(fgm) + 0.5 x sum(fg3m)) / sum(fga)                       F-12
    tov_pct = sum(tov) / (sum(fga) + 0.44 x sum(fta) + sum(tov))          F-12
    orb_pct = sum(oreb) / (sum(oreb) + sum(opp_dreb))                      F-12
    ftr    = sum(ftm) / sum(fga)                                           F-12
    opp_*  = the same four with the opponent rows (defensive Four Factors) F-12
    fg3a_rate = sum(fg3a) / sum(fga), fta_rate = sum(fta) / sum(fga),
    ast_rate = sum(ast) / sum(fgm)                                         F-13
  A zero denominator gives NULL. A window N in {5, 10, 20} is the team's last N visible
  games of the same season in start order (regular, play-in and playoffs; never the Cup
  final, M-3); with k < N games the k games are used and n_games_N = k; k = 0 gives NULL.
- Season to date (F-06): the same definitions over every visible game of the season
  (postseason included, design 2-4), win_pct_std = wins / games_played, games_played.
- Previous season (F-07): prev_net, prev_win_pct over the team's visible games of the
  season before the target's; prev_postseason = the team played a play-in or playoff game
  that season (schedule). NULL when that season has no data (2023-24 sees 2022-23, P-026).
- Rest (F-08, schedule only, Cup final included): days_rest = D(g) - D(previous game of the
  season), is_b2b = (days_rest == 1), games_last_7d = games with D(g) - 7 <= D(h) < D(g).
  The season's first game: days_rest NULL, is_b2b false, games_last_7d 0.
- home_adv = 0 if neutral else 1 (F-09, D-046).
- Elo (F-10): presagio.models.elo.season_ratings over every visible result of every season
  in start order (2022-23 warm-up included, D-055 item 1; the pending season break applied
  on an opening day, D-063 item 2); elo_diff_adj = elo_home + hca x home_adv - elo_away,
  elo_prob = 1 / (1 + 10^(-elo_diff_adj / 400)) (the same function as baseline B2).
- Context (F-15): season_day = D(g) - first regular-season date, game_no = games_played + 1,
  is_playoffs = game_type in {playin, playoffs}.
- League (F-17): league_total_100 = mean total of the last 100 visible games of any season
  and type, league_home_rate_std = home win rate of the target season's visible non-neutral
  games; NULL on an empty sample.
- Floats are rounded to `decimals` (6) places so a jsonb round trip reproduces them exactly
  (the feature_drift check compares with ==); ints and bools stay as they are. Differences
  diff_<name> = home - away exist for numeric team values only and are taken from the
  rounded sides, so diff == round(home - away, 6) holds on the stored row.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from statistics import fmean
from typing import Any

from presagio.features.manifest import (
    GAME_KEYS,
    POSTSEASON_TYPES,
    PREDICTED_GAME_TYPES,
    FeatureVersion,
    team_names,
)
from presagio.ingestion.possessions import FTA_WEIGHT, game_possessions
from presagio.models.elo import expected_home, season_ratings

REGULATION_MINUTES = 240


@dataclass(frozen=True)
class StatLine:
    """One team's row of team_game_stats_primary for one game (the counts fv1 reads)."""

    team_id: int
    minutes: int
    pts: int
    fgm: int
    fga: int
    fg3m: int
    fg3a: int
    ftm: int
    fta: int
    oreb: int
    dreb: int
    ast: int
    tov: int


@dataclass(frozen=True)
class PriorGame:
    """A game on the schedule before the target date (rule R1: D(h) < D(g)).

    The score and both stat lines are attached only when the game passed rule R2 at the
    caller's as_of (final, start + lag <= as_of, both rows in the primary view); a final
    game whose stats are not in yet is `available` without `stats` (counted as finished in
    the input completeness, used nowhere else).
    """

    game_id: str
    season: str
    game_type: str
    et_date: date
    start_time_utc: datetime
    home_team_id: int
    away_team_id: int
    is_neutral: bool
    available: bool = False
    home_score: int | None = None
    away_score: int | None = None
    stats: Mapping[int, StatLine] | None = None

    @property
    def visible(self) -> bool:
        """Usable as a result: predicted type, R2 time rule passed, both stat lines."""
        return (
            self.game_type in PREDICTED_GAME_TYPES
            and self.available
            and self.stats is not None
            and self.home_score is not None
            and self.away_score is not None
        )

    @property
    def home_won(self) -> bool:
        """Home team won (visible games only)."""
        assert self.home_score is not None and self.away_score is not None
        return self.home_score > self.away_score

    @property
    def total(self) -> int:
        """Total points (visible games only)."""
        assert self.home_score is not None and self.away_score is not None
        return self.home_score + self.away_score

    def involves(self, team_id: int) -> bool:
        """The team played in this game."""
        return team_id in (self.home_team_id, self.away_team_id)


@dataclass(frozen=True)
class TargetGame:
    """The game features are computed for: schedule only, no score, no status."""

    game_id: str
    season: str
    game_type: str
    et_date: date
    start_time_utc: datetime
    home_team_id: int
    away_team_id: int
    is_neutral: bool


@dataclass(frozen=True)
class Completeness:
    """Same-season finished games of a team (R1 + R2 time rule) and how many have stats."""

    with_stats: int
    finished: int


@dataclass(frozen=True)
class Computed:
    """The value dict of one target game plus the counts the input fingerprint records."""

    values: dict[str, Any]
    home: Completeness
    away: Completeness


def previous_season(season: str) -> str:
    """'2023-24' -> '2022-23'."""
    first = int(season[:4]) - 1
    return f"{first}-{(first + 1) % 100:02d}"


def ratio(numerator: float, denominator: float) -> float | None:
    """numerator / denominator, NULL on a zero denominator (design 3-1)."""
    return None if denominator == 0 else numerator / denominator


def round_value(value: Any, decimals: int) -> Any:
    """Round a float to `decimals` places (-0.0 becomes 0.0); ints, bools and None as is."""
    if isinstance(value, float):
        return round(value, decimals) + 0.0
    return value


def _lines(game: PriorGame, team_id: int) -> tuple[StatLine, StatLine] | None:
    """(own line, opponent line) of a visible game, or None if the team's row is missing."""
    assert game.stats is not None
    own = game.stats.get(team_id)
    others = [line for tid, line in game.stats.items() if tid != team_id]
    if own is None or len(others) != 1:
        return None
    return own, others[0]


def window_values(games: Sequence[tuple[StatLine, StatLine]]) -> dict[str, float | None]:
    """The F-01 ... F-05, F-12, F-13 sum-based values over the given (own, opponent) lines."""
    k = len(games)
    names = (
        "ortg", "drtg", "net", "pace", "margin", "efg", "tov_pct", "orb_pct", "ftr", "opp_efg",
        "opp_tov_pct", "opp_orb_pct", "opp_ftr", "fg3a_rate", "fta_rate", "ast_rate",
    )  # fmt: skip
    if k == 0:
        return dict.fromkeys(names)
    poss = 0.0
    minutes = 0.0
    s = dict.fromkeys(
        ("pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "ast", "tov"), 0
    )
    o = dict(s)
    for own, opp in games:
        game_poss = game_possessions(own, opp)
        assert game_poss is not None  # StatLine counts are never None
        poss += game_poss
        minutes += own.minutes / REGULATION_MINUTES
        for name in s:
            s[name] += getattr(own, name)
            o[name] += getattr(opp, name)
    ortg = ratio(100.0 * s["pts"], poss)
    drtg = ratio(100.0 * o["pts"], poss)
    return {
        "ortg": ortg,
        "drtg": drtg,
        "net": None if ortg is None or drtg is None else ortg - drtg,
        "pace": ratio(poss, minutes),
        "margin": (s["pts"] - o["pts"]) / k,
        "efg": ratio(s["fgm"] + 0.5 * s["fg3m"], s["fga"]),
        "tov_pct": ratio(s["tov"], s["fga"] + FTA_WEIGHT * s["fta"] + s["tov"]),
        "orb_pct": ratio(s["oreb"], s["oreb"] + o["dreb"]),
        "ftr": ratio(s["ftm"], s["fga"]),
        "opp_efg": ratio(o["fgm"] + 0.5 * o["fg3m"], o["fga"]),
        "opp_tov_pct": ratio(o["tov"], o["fga"] + FTA_WEIGHT * o["fta"] + o["tov"]),
        "opp_orb_pct": ratio(o["oreb"], o["oreb"] + s["dreb"]),
        "opp_ftr": ratio(o["ftm"], o["fga"]),
        "fg3a_rate": ratio(s["fg3a"], s["fga"]),
        "fta_rate": ratio(s["fta"], s["fga"]),
        "ast_rate": ratio(s["ast"], s["fgm"]),
    }


def team_season(
    results: Sequence[PriorGame], team_id: int, season: str
) -> tuple[list[tuple[StatLine, StatLine]], int]:
    """The team's visible games of a season in start order: (own, opp) lines and wins."""
    lines: list[tuple[StatLine, StatLine]] = []
    wins = 0
    for g in results:
        if g.season != season or not g.involves(team_id):
            continue
        pair = _lines(g, team_id)
        if pair is None:
            continue
        lines.append(pair)
        wins += int(g.home_won == (g.home_team_id == team_id))
    return lines, wins


def team_values(
    target: TargetGame,
    team_id: int,
    results: Sequence[PriorGame],
    schedule: Sequence[PriorGame],
    spec: FeatureVersion,
) -> tuple[dict[str, Any], Completeness]:
    """Every team-side value (names without the side prefix) and the input completeness."""
    out: dict[str, Any] = {}
    season_lines, wins = team_season(results, team_id, target.season)
    for n in spec.windows:
        window = window_values(season_lines[-n:])
        out.update({f"{name}_{n}": value for name, value in window.items()})
        out[f"n_games_{n}"] = len(season_lines[-n:])
    whole = window_values(season_lines)
    games_played = len(season_lines)
    out.update({f"{name}_std": whole[name] for name in ("ortg", "drtg", "net", "pace", "margin")})
    out["win_pct_std"] = ratio(wins, games_played)
    out["games_played"] = games_played
    out["game_no"] = games_played + 1
    # F-07: the previous season, visible in full (every date is before D(g)).
    prev = previous_season(target.season)
    prev_lines, prev_wins = team_season(results, team_id, prev)
    prev_schedule = [g for g in schedule if g.season == prev and g.involves(team_id)]
    prev_whole = window_values(prev_lines)
    out["prev_net"] = prev_whole["net"]
    out["prev_win_pct"] = ratio(prev_wins, len(prev_lines))
    out["prev_postseason"] = (
        any(g.game_type in POSTSEASON_TYPES for g in prev_schedule) if prev_schedule else None
    )
    # F-08: schedule of the same season, Cup final included, results not needed.
    dates = [g.et_date for g in schedule if g.season == target.season and g.involves(team_id)]
    days_rest = (target.et_date - max(dates)).days if dates else None
    out["days_rest"] = days_rest
    out["is_b2b"] = days_rest == 1
    out["games_last_7d"] = sum((target.et_date - d).days <= 7 for d in dates)
    finished = [
        g
        for g in schedule
        if g.season == target.season
        and g.game_type in PREDICTED_GAME_TYPES
        and g.involves(team_id)
        and g.available
    ]
    return out, Completeness(with_stats=games_played, finished=len(finished))


def compute_values(
    target: TargetGame,
    prior: Sequence[PriorGame],
    spec: FeatureVersion,
    first_regular_date: date | None,
) -> Computed:
    """The fv1 value dict of `target` from the prior games (start order), rounded.

    The caller applies R1 when loading; this refuses a prior game on or after the target's
    date anyway (ValueError), so a same-date game can never slip into a schedule feature.
    """
    late = [g.game_id for g in prior if g.et_date >= target.et_date]
    if late:
        raise ValueError(f"prior games must precede the target date (rule R1): {late[:3]}")
    schedule = tuple(prior)
    results = tuple(g for g in prior if g.visible)
    home, home_done = team_values(target, target.home_team_id, results, schedule, spec)
    away, away_done = team_values(target, target.away_team_id, results, schedule, spec)
    raw: dict[str, Any] = {}
    for name, has_diff in team_names(spec.windows):
        # Sides are rounded first so that diff == round(home - away) holds on the stored row.
        h, a = round_value(home[name], spec.decimals), round_value(away[name], spec.decimals)
        raw[f"home_{name}"] = h
        raw[f"away_{name}"] = a
        if has_diff:
            raw[f"diff_{name}"] = None if h is None or a is None else h - a
    home_adv = 0 if target.is_neutral else 1
    elo = spec.elo_constants
    ratings = season_ratings(results, target.season, **elo)
    elo_home = ratings.get(target.home_team_id, elo["initial"])
    elo_away = ratings.get(target.away_team_id, elo["initial"])
    last = results[-spec.league_window :]
    season_home = [g.home_won for g in results if g.season == target.season and not g.is_neutral]
    raw.update(
        home_adv=home_adv,
        elo_home=elo_home,
        elo_away=elo_away,
        elo_diff_adj=elo_home + elo["hca"] * home_adv - elo_away,
        elo_prob=expected_home(elo_home, elo_away, hca=elo["hca"], is_neutral=target.is_neutral),
        season_day=None
        if first_regular_date is None
        else (target.et_date - first_regular_date).days,
        is_playoffs=target.game_type in POSTSEASON_TYPES,
        league_total_100=fmean(g.total for g in last) if last else None,
        league_home_rate_std=fmean(float(w) for w in season_home) if season_home else None,
    )
    assert tuple(raw) == tuple(k for k in spec.keys), "value keys must follow the manifest"
    assert set(GAME_KEYS) <= set(raw)
    values = {key: round_value(raw[key], spec.decimals) for key in spec.keys}
    return Computed(values=values, home=home_done, away=away_done)
