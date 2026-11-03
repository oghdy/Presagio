"""Hand-calculated fv1 values without a database (P-035 criterion 7 (a)-(h)).

Every expected number below is worked out by hand from the formulas in
presagio.features.fv1 (design 3-1): possessions 100 per team-game unless said otherwise
(fga 90 - oreb 10 + tov 9 + 0.44 x fta 25 = 100).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest

from presagio.features.fv1 import (
    PriorGame,
    StatLine,
    TargetGame,
    compute_values,
    previous_season,
    round_value,
    window_values,
)
from presagio.features.manifest import (
    FEATURE_VERSIONS,
    GAME_KEYS,
    FeatureVersion,
    build_keys,
    team_names,
)
from presagio.models.elo import elo_ratings, expected_home, regress

SPEC = FEATURE_VERSIONS["fv1"]
A, B, C, D = 1, 2, 3, 4
SEASON = "2023-24"
PREV = "2022-23"
D0 = date(2023, 12, 1)


def day(offset: int) -> date:
    """D0 + offset days."""
    return D0 + timedelta(days=offset)


def tip(on: date) -> datetime:
    """A tip-off instant on an ET date (23:30 UTC = 18:30 ET)."""
    return datetime.combine(on, time(23, 30), tzinfo=UTC)


def line(team: int, pts: int, **counts: int) -> StatLine:
    """A stat line with 100 possessions unless the counts say otherwise."""
    values: dict[str, int] = dict(
        minutes=240, fgm=40, fga=90, fg3m=10, fg3a=30, ftm=20, fta=25, oreb=10, dreb=30, ast=20,
        tov=9,
    )  # fmt: skip
    values.update(counts)
    return StatLine(team_id=team, pts=pts, **values)


def game(
    n: int,
    on: date,
    home: int,
    away: int,
    home_line: StatLine | None = None,
    away_line: StatLine | None = None,
    *,
    season: str = SEASON,
    game_type: str = "regular",
    is_neutral: bool = False,
) -> PriorGame:
    """A prior game; with both lines it is a visible result, without them schedule only."""
    visible = home_line is not None and away_line is not None
    return PriorGame(
        game_id=f"g{n}",
        season=season,
        game_type=game_type,
        et_date=on,
        start_time_utc=tip(on),
        home_team_id=home,
        away_team_id=away,
        is_neutral=is_neutral,
        available=visible,
        home_score=home_line.pts if visible else None,
        away_score=away_line.pts if visible else None,
        stats={home: home_line, away: away_line} if visible else None,
    )


def target(
    on: date,
    home: int = A,
    away: int = B,
    *,
    season: str = SEASON,
    game_type: str = "regular",
    is_neutral: bool = False,
) -> TargetGame:
    """The game the features are for."""
    return TargetGame(
        game_id="target",
        season=season,
        game_type=game_type,
        et_date=on,
        start_time_utc=tip(on),
        home_team_id=home,
        away_team_id=away,
        is_neutral=is_neutral,
    )


def values(
    t: TargetGame,
    prior: list[PriorGame],
    first_regular: date | None = None,
    spec: FeatureVersion = SPEC,
) -> dict[str, Any]:
    """compute_values(...).values."""
    return compute_values(t, prior, spec, first_regular).values


# Two games of A: 110-100 at home against B, then 100-90 away at B.
TWO_GAMES = [
    game(1, day(0), A, B, line(A, 110), line(B, 100)),
    game(2, day(2), B, A, line(B, 90), line(A, 100)),
]


def test_a_sum_based_efficiency_margin_and_pace() -> None:
    """(a) ortg_5 = 100 x 210 / 200 = 105, drtg_5 = 100 x 190 / 200 = 95, net 10, margin
    (10 + 10) / 2 = 10, pace = 200 / (480 / 240) = 100; B's side mirrors it."""
    v = values(target(day(4)), TWO_GAMES)
    assert (v["home_ortg_5"], v["home_drtg_5"], v["home_net_5"]) == (105.0, 95.0, 10.0)
    assert (v["home_margin_5"], v["home_pace_5"], v["home_n_games_5"]) == (10.0, 100.0, 2)
    assert (v["away_ortg_5"], v["away_drtg_5"], v["away_net_5"]) == (95.0, 105.0, -10.0)
    assert (v["diff_ortg_5"], v["diff_net_5"], v["diff_margin_5"]) == (10.0, 20.0, 20.0)
    assert v["home_ortg_20"] == v["home_ortg_std"] == 105.0  # the same two games


def test_a_overtime_lowers_pace_by_the_minutes_played() -> None:
    """(a) One 265-minute game: pace = 200 / (240/240 + 265/240), not 200 / 2."""
    prior = [
        TWO_GAMES[0],
        game(2, day(2), B, A, line(B, 90, minutes=265), line(A, 100, minutes=265)),
    ]
    v = values(target(day(4)), prior)
    assert v["home_pace_5"] == round(200 / (1 + 265 / 240), 6)
    assert v["home_pace_5"] < 100.0 and v["home_ortg_5"] == 105.0  # efficiency unchanged


def test_b_rates_are_ratios_of_sums_not_means_of_ratios() -> None:
    """(b) Possessions 80 then 120 with ortg 100 and 110 per game: the sum-based rate is
    100 x 212 / 200 = 106, while the mean of the per-game rates would be 105."""
    prior = [
        game(1, day(0), A, B, line(A, 80, fga=70), line(B, 70, fga=70)),
        game(2, day(2), A, B, line(A, 132, fga=110), line(B, 100, fga=110)),
    ]
    v = values(target(day(4)), prior)
    assert v["home_ortg_5"] == 106.0
    assert v["home_ortg_5"] != (100.0 + 110.0) / 2


def test_c_short_window_uses_the_games_available_and_reports_the_count() -> None:
    """(c) Three games: n_games_5 = 3 and the window equals the season-to-date values;
    no game: NULL values with n_games_5 = 0, games_played 0, game_no 1."""
    prior = [
        game(1, day(0), A, B, line(A, 100), line(B, 90)),
        game(2, day(2), A, C, line(A, 110), line(C, 90)),
        game(3, day(4), D, A, line(D, 95), line(A, 100)),
    ]
    v = values(target(day(6)), prior)
    assert (v["home_n_games_5"], v["home_n_games_10"], v["home_n_games_20"]) == (3, 3, 3)
    assert v["home_ortg_5"] == v["home_ortg_std"] == round(100 * 310 / 300, 6)
    assert v["home_games_played"] == 3 and v["home_game_no"] == 4
    assert v["home_win_pct_std"] == 1.0
    empty = values(target(day(6), home=C, away=D), [])
    assert empty["home_ortg_5"] is None and empty["home_n_games_5"] == 0
    assert empty["home_games_played"] == 0 and empty["home_game_no"] == 1
    assert empty["home_win_pct_std"] is None and empty["home_ortg_std"] is None
    assert empty["diff_ortg_5"] is None


def test_c_windows_never_cross_a_season_boundary() -> None:
    """(c) Last season's games feed prev_* only: the new season's first game has NULL
    windows and n_games = 0 although three visible games of the team exist."""
    prior = [
        game(n, day(2 * n), A, B, line(A, 100 + n), line(B, 90), season=PREV) for n in range(3)
    ]
    v = values(target(day(300)), prior)
    assert v["home_n_games_5"] == 0 and v["home_ortg_5"] is None
    assert v["home_games_played"] == 0 and v["home_ortg_std"] is None
    assert v["home_prev_net"] is not None and v["home_prev_win_pct"] == 1.0


def test_d_four_factors_and_attempt_rates_by_hand() -> None:
    """(d) A: efg (40 + 5)/90 = 0.5, tov% 9/(90 + 11 + 9), orb% 10/(10 + 36), ftr 20/90,
    fg3a 30/90, fta 25/90, ast 20/40; the opponent line gives the opp_* values."""
    opp = line(B, 100, fgm=35, fg3m=5, fga=80, ftm=25, fta=30, oreb=8, dreb=36, ast=30, tov=12)
    v = values(target(day(2)), [game(1, day(0), A, B, line(A, 110), opp)])
    assert v["home_efg_5"] == 0.5
    assert v["home_tov_pct_5"] == round(9 / (90 + 0.44 * 25 + 9), 6)
    assert v["home_orb_pct_5"] == round(10 / (10 + 36), 6)
    assert v["home_ftr_5"] == round(20 / 90, 6)
    assert v["home_fg3a_rate_5"] == round(30 / 90, 6)
    assert v["home_fta_rate_5"] == round(25 / 90, 6)
    assert v["home_ast_rate_5"] == 0.5
    assert v["home_opp_efg_5"] == round((35 + 2.5) / 80, 6)
    assert v["home_opp_tov_pct_5"] == round(12 / (80 + 0.44 * 30 + 12), 6)
    assert v["home_opp_orb_pct_5"] == round(8 / (8 + 30), 6)
    assert v["home_opp_ftr_5"] == round(25 / 80, 6)
    # B's offensive values are A's opp_* values and the other way round.
    assert v["away_efg_5"] == v["home_opp_efg_5"] and v["away_opp_orb_pct_5"] == v["home_orb_pct_5"]


def test_d_zero_denominators_give_null() -> None:
    """(d) No attempts at all: every ratio with a zero denominator is NULL, the game is
    still counted."""
    zero = dict(fgm=0, fga=0, fg3m=0, fg3a=0, ftm=0, fta=0, oreb=0, tov=0, ast=0)
    both = [game(1, day(0), A, B, line(A, 0, **zero, dreb=0), line(B, 0, **zero, dreb=0))]
    v = values(target(day(2)), both)
    names = (
        "efg", "tov_pct", "orb_pct", "ftr", "opp_efg", "opp_tov_pct", "opp_orb_pct", "opp_ftr",
        "fg3a_rate", "fta_rate", "ast_rate", "ortg", "drtg", "net",
    )  # fmt: skip
    for name in names:
        assert v[f"home_{name}_5"] is None, name
    assert v["home_pace_5"] == 0.0  # 0 possessions over 240 minutes: the denominator is not 0
    assert v["home_n_games_5"] == 1 and v["home_margin_5"] == 0.0
    w = window_values([(line(A, 100, fga=0, oreb=0, tov=0, fta=0), line(B, 90))])
    assert w["ortg"] == 200.0 and w["efg"] is None  # own possessions 0, opp 100: game 50
    assert w["pace"] == 50.0


def test_e_rest_days_back_to_back_and_games_in_the_last_7_days() -> None:
    """(e) A game the day before: days_rest 1 and is_b2b; games_last_7d counts
    D(g) - 7 <= D(h) < D(g) (7 days ago in, 8 days ago out)."""
    prior = [game(1, day(-8), A, C), game(2, day(-7), C, A), game(3, day(-1), A, B)]
    v = values(target(day(0)), prior)
    assert (v["home_days_rest"], v["home_is_b2b"], v["home_games_last_7d"]) == (1, True, 2)
    assert v["away_days_rest"] == 1 and v["away_games_last_7d"] == 1  # B played once
    assert v["diff_days_rest"] == 0 and v["diff_games_last_7d"] == 1
    assert "diff_is_b2b" not in v
    two_days = values(target(day(0)), [game(1, day(-2), A, B)])
    assert (two_days["home_days_rest"], two_days["home_is_b2b"]) == (2, False)


def test_e_the_cup_final_counts_as_the_previous_game_but_not_as_a_result() -> None:
    """(e) D-049 M-3: a Cup final the day before gives days_rest 1 while its stats never
    enter a window (n_games stays 0 even though the lines are there)."""
    cup = game(1, day(-1), A, C, line(A, 100), line(C, 90), game_type="cup_final", is_neutral=True)
    v = values(target(day(0)), [cup])
    assert (v["home_days_rest"], v["home_is_b2b"], v["home_games_last_7d"]) == (1, True, 1)
    assert v["home_n_games_5"] == 0 and v["home_games_played"] == 0
    assert v["elo_home"] == 1500.0 and v["league_total_100"] is None


def test_e_the_seasons_first_game_has_no_rest_values() -> None:
    """(e) No earlier game this season: days_rest NULL, is_b2b false, games_last_7d 0,
    even with games of the previous season on the schedule."""
    v = values(target(day(0)), [game(1, day(-3), A, B, season=PREV)])
    assert (v["home_days_rest"], v["home_is_b2b"], v["home_games_last_7d"]) == (None, False, 0)
    assert v["diff_days_rest"] is None


def test_e_a_same_date_prior_game_is_refused_by_the_pure_layer() -> None:
    """(e) Rule R1 belongs to the loader, but a prior game on the target date (or later)
    is a ValueError here too, so it can never count as "the previous game"."""
    with pytest.raises(ValueError, match="R1"):
        values(target(day(0)), [game(1, day(0), C, D)])
    with pytest.raises(ValueError, match="R1"):
        values(target(day(0)), [game(1, day(1), C, D, line(C, 100), line(D, 90))])


def test_f_neutral_site_has_no_home_advantage() -> None:
    """(f) home_adv 0 on a neutral site: elo_diff_adj = elo_home - elo_away and elo_prob
    is the neutral expectation; otherwise home_adv 1 adds hca = 100."""
    prior = [game(1, day(0), A, B, line(A, 110), line(B, 100))]
    neutral = values(target(day(2), is_neutral=True), prior)
    home = values(target(day(2)), prior)
    assert neutral["home_adv"] == 0 and home["home_adv"] == 1
    assert neutral["elo_diff_adj"] == round(neutral["elo_home"] - neutral["elo_away"], 6)
    assert home["elo_diff_adj"] == round(home["elo_home"] + 100 - home["elo_away"], 6)
    assert neutral["elo_prob"] == round(
        expected_home(neutral["elo_home"], neutral["elo_away"], hca=100.0, is_neutral=True), 6
    )
    assert neutral["elo_prob"] < home["elo_prob"]


def test_elo_by_hand_and_the_season_break() -> None:
    """F-10: one home win of A from 1500/1500 moves A by 20 x (1 - 0.640065); a target of
    the next season sees the break 0.75 x R + 0.25 x 1505 applied (D-063 item 2)."""
    prior = [game(1, day(0), A, B, line(A, 110), line(B, 100))]
    e = 1 / (1 + 10 ** (-100 / 400))
    v = values(target(day(2)), prior)
    assert v["elo_home"] == round(1500 + 20 * (1 - e), 6)
    assert v["elo_away"] == round(1500 - 20 * (1 - e), 6)
    assert v["elo_prob"] == round(
        expected_home(v["elo_home"], v["elo_away"], hca=100.0, is_neutral=False), 6
    )
    ratings = elo_ratings([g for g in prior], **SPEC.elo_constants)
    regress(ratings, carry=0.75, mean=1505.0)
    next_season = values(target(day(300), season="2024-25"), prior)
    assert next_season["elo_home"] == round(ratings[A], 6) != v["elo_home"]


def test_g_previous_season_values_or_null() -> None:
    """(g) prev_net and prev_win_pct over the previous season's games; prev_postseason from
    its schedule (A reached the playoffs, B did not); all NULL without that season."""
    prev = [
        game(1, day(-300), A, B, line(A, 110), line(B, 100), season=PREV),
        game(2, day(-298), B, A, line(B, 105), line(A, 100), season=PREV),
        game(3, day(-296), A, B, line(A, 120), line(B, 100), season=PREV),
        game(4, day(-200), A, C, season=PREV, game_type="playoffs"),
    ]
    v = values(target(day(0)), prev)
    assert v["home_prev_net"] == round(100 * (330 - 305) / 300, 6)
    assert v["home_prev_win_pct"] == round(2 / 3, 6) and v["away_prev_win_pct"] == round(1 / 3, 6)
    assert v["home_prev_postseason"] is True and v["away_prev_postseason"] is False
    assert v["diff_prev_net"] == round(v["home_prev_net"] - v["away_prev_net"], 6)
    assert "diff_prev_postseason" not in v
    none = values(target(day(0)), [game(1, day(-2), A, B, line(A, 100), line(B, 90))])
    assert (none["home_prev_net"], none["home_prev_win_pct"], none["home_prev_postseason"]) == (
        None, None, None,
    )  # fmt: skip
    # Two seasons back is not "previous".
    older = values(
        target(day(0)), [game(1, day(-700), A, B, line(A, 100), line(B, 90), season="2021-22")]
    )
    assert older["home_prev_net"] is None and older["home_prev_postseason"] is None


def test_previous_season_label() -> None:
    """'2023-24' -> '2022-23', across the century mark too."""
    assert previous_season("2023-24") == "2022-23"
    assert previous_season("2000-01") == "1999-00"


def test_h_keys_follow_the_manifest_and_floats_have_six_decimals() -> None:
    """(h) The value keys are the manifest's, in order; every float is rounded to 6
    places; diff_* exists for numeric team values only and equals home - away."""
    prior = [
        game(1, day(0), A, B, line(A, 100, fga=80), line(B, 90, fga=80)),  # poss 90: 1/9 rates
        game(2, day(2), C, D, line(C, 100), line(D, 90)),
    ]
    v = values(target(day(4)), prior, first_regular=day(0))
    assert list(v) == list(SPEC.keys) == list(build_keys(SPEC.windows))
    assert set(GAME_KEYS) <= set(v)
    assert v["home_ortg_5"] == 111.111111  # 100 x 100 / 90 rounded
    for key, value in v.items():
        if isinstance(value, float):
            assert round(value, 6) == value, key
            assert not (value == 0 and str(value).startswith("-")), key
    for name, has_diff in team_names(SPEC.windows):
        h, a = v[f"home_{name}"], v[f"away_{name}"]
        if has_diff:
            assert not isinstance(h, bool) and not isinstance(a, bool), name
            expected = None if h is None or a is None else round(h - a, 6)
            assert v[f"diff_{name}"] == expected, name
        else:
            assert f"diff_{name}" not in v, name
            assert name.startswith("n_games_") or isinstance(h, bool | type(None)), name
    assert v["season_day"] == 4 and v["is_playoffs"] is False
    assert round_value(-0.0, 6) == 0.0 and str(round_value(-0.0, 6)) == "0.0"
    assert round_value(7, 6) == 7 and round_value(True, 6) is True and round_value(None, 6) is None


def test_league_values_use_every_visible_game_of_any_season_but_only_the_last_100() -> None:
    """F-17: league_total_100 averages the last 100 visible totals of any season and type;
    league_home_rate_std counts the target season's non-neutral games only."""
    prior = [game(0, day(-400), A, B, line(A, 500), line(B, 500), season=PREV)]  # total 1000
    prior += [
        game(n, day(-300 + n), A, B, line(A, 100), line(B, 90), season=PREV) for n in range(1, 59)
    ]
    prior += [game(100 + n, day(n), C, D, line(C, 100), line(D, 90)) for n in range(41)]
    prior.append(game(200, day(42), C, D, line(C, 90), line(D, 100), is_neutral=True))
    v = values(target(day(44)), prior)
    assert len([g for g in prior if g.visible]) == 101
    assert v["league_total_100"] == 190.0  # the 1000 fell out of the window
    assert v["league_home_rate_std"] == 1.0  # 41 home wins this season; the neutral loss is out
    assert v["is_playoffs"] is False
    playoffs = values(target(day(44), game_type="playoffs"), prior)
    assert playoffs["is_playoffs"] is True
    assert values(target(day(44)), [])["league_total_100"] is None


def test_the_manifest_lists_fv1_with_the_design_constants() -> None:
    """Criterion 1: windows 5/10/20, D-035 possessions, Delta 3 h, league window 100, M-7
    Elo constants, 6 decimals; keys built once from the windows."""
    assert SPEC.windows == (5, 10, 20) and SPEC.poss_formula == "d035"
    assert SPEC.availability_lag_hours == 3 and SPEC.league_window == 100
    assert dict(SPEC.elo) == {"k": 20, "hca": 100, "carry": 0.75, "mean": 1505, "initial": 1500}
    assert SPEC.decimals == 6
    assert len(SPEC.keys) == len(set(SPEC.keys))
    assert SPEC.keys == build_keys((5, 10, 20))
