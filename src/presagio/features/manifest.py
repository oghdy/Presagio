"""Feature version manifest (modeling design v1.2 3-0 "version strategy", P-035).

One feature set has one version string ("fv1", "fv2", ...). Everything that defines the set
lives here, in code: the key list (the single source of truth that stored rows, the pure
computation and the tests are checked against), the rolling window lengths, the possession
formula id, the availability lag Delta of rule R2, the league window of F-17, the Elo
constants of F-10 and the rounding of float values. Changing any of these means a new
version (design 3-0, 8-1: stored rows are never overwritten).

Key naming (design 3-0, P-035 criterion 2): a team value is `home_<name>` / `away_<name>`,
its difference `diff_<name> = home - away` (numeric team values only; booleans and the
window counts `n_games_<N>` have no diff). Game-level values carry no prefix.

Design references: 2-2 (R1, R2), 2-4 (cold start, season boundary), 3-0, 3-1 (F-01 ... F-17),
8-1 (features table), D-035 (possessions), D-049 M-7 (Elo constants), M-10 (Delta = 3 h).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

# Game types a feature row is computed for and whose results feed the stat, Elo and league
# features (D-028 A-4, D-049 M-3). The Cup final is read by the schedule features only.
PREDICTED_GAME_TYPES = ("regular", "playin", "playoffs")
SCHEDULE_GAME_TYPES = ("regular", "playin", "playoffs", "cup_final")
POSTSEASON_TYPES = ("playin", "playoffs")

# Team values with a float result per rolling window N (F-01 ... F-05, F-12, F-13).
WINDOW_FLOAT_NAMES = (
    "ortg",
    "drtg",
    "net",
    "pace",
    "margin",
    "efg",
    "tov_pct",
    "orb_pct",
    "ftr",
    "opp_efg",
    "opp_tov_pct",
    "opp_orb_pct",
    "opp_ftr",
    "fg3a_rate",
    "fta_rate",
    "ast_rate",
)
# Season-to-date team values (F-06): the F-01 ... F-05 definitions plus the win rate.
SEASON_FLOAT_NAMES = ("ortg_std", "drtg_std", "net_std", "pace_std", "margin_std", "win_pct_std")
# Integer team values that get a diff too.
TEAM_INT_NAMES = ("games_played", "game_no", "days_rest", "games_last_7d")
# Previous-season team values (F-07).
PREV_FLOAT_NAMES = ("prev_net", "prev_win_pct")
# Team booleans (no diff).
TEAM_BOOL_NAMES = ("is_b2b", "prev_postseason")
# Game-level keys (F-09, F-10, F-15, F-17): no side prefix, no diff.
GAME_KEYS = (
    "home_adv",
    "elo_home",
    "elo_away",
    "elo_diff_adj",
    "elo_prob",
    "season_day",
    "is_playoffs",
    "league_total_100",
    "league_home_rate_std",
)


def team_names(windows: tuple[int, ...]) -> tuple[tuple[str, bool], ...]:
    """(name, has_diff) for every team value, in a fixed order."""
    names: list[tuple[str, bool]] = []
    for n in windows:
        names += [(f"{name}_{n}", True) for name in WINDOW_FLOAT_NAMES]
        names.append((f"n_games_{n}", False))
    names += [(name, True) for name in SEASON_FLOAT_NAMES]
    names += [(name, True) for name in TEAM_INT_NAMES]
    names += [(name, True) for name in PREV_FLOAT_NAMES]
    names += [(name, False) for name in TEAM_BOOL_NAMES]
    return tuple(names)


def build_keys(windows: tuple[int, ...]) -> tuple[str, ...]:
    """The full key list of a version: home_*, away_*, diff_* (numeric only), game keys."""
    keys: list[str] = []
    for name, has_diff in team_names(windows):
        keys += [f"home_{name}", f"away_{name}"]
        if has_diff:
            keys.append(f"diff_{name}")
    keys += GAME_KEYS
    return tuple(keys)


@dataclass(frozen=True)
class FeatureVersion:
    """Everything that defines one feature set version."""

    keys: tuple[str, ...]
    windows: tuple[int, ...]
    poss_formula: str
    availability_lag_hours: int
    league_window: int
    elo: Mapping[str, float]
    decimals: int

    @property
    def elo_constants(self) -> dict[str, float]:
        """Elo keyword arguments (k, hca, carry, mean, initial) as floats."""
        return {name: float(self.elo[name]) for name in ("k", "hca", "carry", "mean", "initial")}


FEATURE_VERSIONS: dict[str, FeatureVersion] = {
    "fv1": FeatureVersion(
        keys=build_keys((5, 10, 20)),
        windows=(5, 10, 20),
        poss_formula="d035",
        availability_lag_hours=3,
        league_window=100,
        elo=MappingProxyType(dict(k=20, hca=100, carry=0.75, mean=1505, initial=1500)),
        decimals=6,
    )
}


def feature_version(version: str) -> FeatureVersion:
    """The manifest entry of `version`; KeyError with the known versions otherwise."""
    try:
        return FEATURE_VERSIONS[version]
    except KeyError:
        raise KeyError(
            f"unknown feature version {version!r}; known: {', '.join(sorted(FEATURE_VERSIONS))}"
        ) from None
