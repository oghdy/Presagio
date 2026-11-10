"""compute_features / compute_day on the temp league DB: the time rules as leakage tests
(design 9-3 T1, T2, T3, T4, T5, T7, T12; P-035 criterion 8 (a)-(h)), the independent R1/R2
implementation against presagio.evaluation.timing (criterion 1), and the code boundaries
(tables read, no evaluation import)."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Connection, text

from evaluation.league import (
    CUP_FINAL,
    T4_LATE_GAME,
    T4_NEXT_DAY_GAME,
    League,
    et,
    game_row,
    insert_games,
    insert_stats,
)
from presagio.evaluation import timing
from presagio.evaluation.experiments import EXPERIMENTS
from presagio.evaluation.walkforward import WalkForwardConfig, run_walk_forward
from presagio.features import compute
from presagio.features.compute import (
    FeatureComputeError,
    FeatureRow,
    compute_day,
    compute_features,
    load_history,
)
from presagio.features.manifest import FEATURE_VERSIONS, PREDICTED_GAME_TYPES
from presagio.models.elo import elo_ratings, expected_home, regress

SRC = Path(compute.__file__).resolve().parent
SPEC = FEATURE_VERSIONS["fv1"]
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
DEV = ("2023-24", "2024-25")
G_DAY = date(2023, 12, 13)  # A-B (home B) and C-D both play; 25 earlier dates this season
PIVOT_SEASON_END = date(2024, 5, 1)


def game_type(game_id: str) -> str:
    """The game type the database derives from the id (schema v1: third digit)."""
    return {"2": "regular", "4": "playoffs", "5": "playin", "6": "cup_final"}[game_id[2]]


def as_of(day: date) -> datetime:
    """The backtest boundary of an ET date (07:00 UTC, D-049 M-1)."""
    return timing.backtest_as_of(day)


def games_on(league: League, day: date) -> list[str]:
    """Game ids on an ET date in start order."""
    rows = [g for g in league.games.values() if timing.et_date(g["start_time_utc"]) == day]
    return [g["game_id"] for g in sorted(rows, key=lambda g: g["start_time_utc"])]


def target_days(league: League, seasons: tuple[str, ...]) -> list[date]:
    """ET dates with a predicted-type game of the seasons."""
    days = {
        timing.et_date(g["start_time_utc"])
        for g in league.games.values()
        if g["season"] in seasons and game_type(g["game_id"]) in PREDICTED_GAME_TYPES
    }
    return sorted(days)


def raw_id(conn: Connection) -> int:
    """A raw_api_responses id to hang inserted rows on."""
    return conn.execute(text("SELECT min(raw_response_id) FROM raw_api_responses")).scalar_one()


def make_extreme(conn: Connection, game_ids: list[str], fetched_at: datetime | None = None) -> None:
    """Scores 300-0 and extreme stat lines (pts 300/0, fga 500) for the games."""
    conn.execute(
        text("UPDATE games SET home_score = 300, away_score = 0 WHERE game_id = ANY(:ids)"),
        {"ids": game_ids},
    )
    conn.execute(
        text(
            "UPDATE team_game_stats SET pts = CASE WHEN is_home THEN 300 ELSE 0 END, fga = 500, "
            "fgm = CASE WHEN is_home THEN 140 ELSE 0 END, fg3m = 0, ftm = CASE WHEN is_home "
            "THEN 20 ELSE 0 END, fetched_at = coalesce(:at, fetched_at) WHERE game_id = ANY(:ids)"
        ),
        {"ids": game_ids, "at": fetched_at},
    )


def insert_extreme_games(conn: Connection, league: League, first: date, n: int) -> list[str]:
    """n final A-B games on consecutive dates from `first` with extreme scores and stats."""
    a, b, _, _ = league.teams
    rows = []
    for i in range(n):
        row = game_row(f"0022399{i:03d}", "2023-24", et(first + timedelta(days=i), 20), a, b, i)
        row.update(home_score=200, away_score=50)
        rows.append(row)
    raw = raw_id(conn)
    insert_games(conn, rows, raw)
    for row in rows:
        insert_stats(conn, row, raw)
    ids = [r["game_id"] for r in rows]
    conn.execute(
        text("UPDATE team_game_stats SET fga = 300 WHERE game_id = ANY(:ids)"), {"ids": ids}
    )
    return ids


# --- criterion 1: the independent R1/R2 implementation agrees with evaluation.timing ----


def test_time_rule_constants_match_evaluation_timing() -> None:
    """Delta of the manifest = DEFAULT_AVAILABILITY_LAG, final status 3, backtest as_of
    07:00 UTC (the CLI calls evaluation.timing.backtest_as_of; the feature modules do not)."""
    assert timedelta(hours=SPEC.availability_lag_hours) == timing.DEFAULT_AVAILABILITY_LAG
    assert compute.availability_lag(SPEC) == timing.DEFAULT_AVAILABILITY_LAG
    assert compute.FINAL_STATUS == timing.FINAL_STATUS == 3
    assert time(7, 0, tzinfo=UTC) == timing.BACKTEST_AS_OF_TIME
    assert as_of(date(2024, 1, 11)) == datetime(2024, 1, 11, 7, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "moment",
    [
        datetime(2024, 1, 11, 4, 0, tzinfo=UTC),  # 23:00 ET the day before (EST)
        datetime(2024, 1, 11, 4, 59, 59, tzinfo=UTC),
        datetime(2024, 1, 11, 5, 0, tzinfo=UTC),  # midnight ET
        datetime(2024, 7, 1, 3, 59, tzinfo=UTC),  # EDT: 23:59 ET the day before
        datetime(2024, 7, 1, 4, 0, tzinfo=UTC),
        datetime(2024, 11, 3, 5, 30, tzinfo=UTC),  # the DST switch date
    ],
)
def test_et_date_matches_evaluation_timing(moment: datetime) -> None:
    """D(x) is the same function in both packages."""
    assert compute.et_date(moment) == timing.et_date(moment)


def test_is_visible_matches_is_available_on_a_grid() -> None:
    """R1 and R2 decisions agree with the runner's on every combination around the edges."""
    target = date(2024, 1, 11)
    boundary = as_of(target)
    starts = [
        datetime(2024, 1, 11, 4, 0, tzinfo=UTC),  # ET 01-10 23:00, available exactly at as_of
        datetime(2024, 1, 11, 4, 0, 1, tzinfo=UTC),  # one second later: not available
        datetime(2024, 1, 11, 5, 0, tzinfo=UTC),  # ET 01-11: same date, R1 fails
        datetime(2024, 1, 10, 0, 30, tzinfo=UTC),  # ET 01-09
    ]
    for start in starts:
        for status in (1, 2, 3):
            for has_stats in (False, True):
                for lag in (timedelta(hours=2), timedelta(hours=3), timedelta(hours=4)):
                    ours = compute.is_visible(
                        start_time_utc=start,
                        status_code=status,
                        has_stats=has_stats,
                        target_date=target,
                        as_of=boundary,
                        lag=lag,
                    )
                    theirs = timing.is_available(
                        start_time_utc=start,
                        status_code=status,
                        has_stats=has_stats,
                        target_date=target,
                        as_of=boundary,
                        lag=lag,
                    )
                    assert ours == theirs, (start, status, has_stats, lag)
    assert compute.is_visible(
        start_time_utc=starts[0], status_code=3, has_stats=True, target_date=target,
        as_of=boundary, lag=timedelta(hours=3),
    )  # fmt: skip
    with pytest.raises(ValueError, match="aware"):
        compute.et_date(datetime(2024, 1, 11, 4, 0))


def test_sql_et_date_agrees_with_python_for_every_game(conn: Connection) -> None:
    """The SQL D(h) (AT TIME ZONE) and the Python D(g) (zoneinfo) never disagree."""
    rows = conn.execute(
        text(
            "SELECT start_time_utc, (start_time_utc AT TIME ZONE 'America/New_York')::date AS d "
            "FROM games"
        )
    ).all()
    assert rows
    assert all(compute.et_date(r.start_time_utc) == r.d for r in rows)


# --- code boundaries (grep tests of criterion 1) -------------------------------------


def test_sql_reads_games_and_the_primary_view_only_with_bound_dates() -> None:
    """Every FROM/JOIN in the feature package names games or team_game_stats_primary; no
    teams, odds or prediction table, no status_text; every statement binds :target_date
    (D(g)) and the results statement binds :as_of and :lag."""
    sources = {p.name: p.read_text() for p in SRC.glob("*.py")}
    assert {"manifest.py", "fv1.py", "compute.py", "store.py", "recompute.py", "cli.py"} <= set(
        sources
    )
    for name, source in sources.items():
        if name == "cli.py":
            continue  # the CLI's day listing is scoped by season, tested below
        allowed = {"games", "team_game_stats_primary"} | (
            {"features"} if name == "store.py" else set()
        )
        for table in re.findall(r"\b(?:FROM|JOIN)\s+([a-z_\"]+)", source):
            assert table.strip('"') in allowed, (name, table)
        assert "status_text" not in source.split('"""', 2)[2], name  # outside the docstring
        for forbidden in ("odds_", "odds_closing_lines", "predictions", "model_versions"):
            assert not re.search(rf"\b{forbidden}", source), (name, forbidden)
    statements = re.findall(r'text\(\s*f?"""(.*?)"""', sources["compute.py"], re.DOTALL)
    reading = [s for s in statements if "FROM games" in s and "WHERE game_id = :game_id" not in s]
    assert len(reading) == 4
    assert all(":target_date" in s for s in reading)
    results = next(s for s in statements if "team_game_stats_primary" in s)
    assert ":as_of" in results and ":lag" in results
    assert "status_code = {FINAL_STATUS}" in results and compute.FINAL_STATUS == 3
    for name in ("manifest.py", "fv1.py", "compute.py", "store.py", "recompute.py"):
        code = sources[name].split('"""', 2)[2]  # after the module docstring
        assert "presagio.evaluation" not in code, name


@pytest.mark.parametrize("name", ["SCHEDULE_SQL", "RESULTS_SQL"])
def test_r1_is_strict_in_the_schedule_and_results_sql(name: str) -> None:
    """P-036 finding 3: R1 reads D(h) < D(g), never <=, in both statements. In RESULTS_SQL a
    `<=` is inert today (the schedule statement leaves same-date games out of `prior` and
    load_history drops results not in it: equivalent mutation C6-M1), so only the SQL text
    pins it. Whitespace and line breaks do not matter."""
    sql = getattr(compute, name).text
    assert re.search(r"\)::date\s*<\s*:target_date\b", sql), name
    assert not re.search(r"<=\s*:target_date\b", sql), name
    assert sql.count(":target_date") == 1, name  # no second date condition


def test_r2_is_inclusive_in_the_results_sql() -> None:
    """R2 in RESULTS_SQL: start_time_utc + :lag <= :as_of (equality counts as known, T4), the
    only condition on :as_of."""
    sql = compute.RESULTS_SQL.text
    assert re.search(r"\bg\.start_time_utc\s*\+\s*:lag\s*<=\s*:as_of\b", sql)
    assert sql.count(":as_of") == 1 and sql.count(":lag") == 1


# --- the leakage tests of design 9-3 -----------------------------------------------


def test_t1_time_travel(conn: Connection, league: League) -> None:
    """T1: inserting games after as_of, changing g's own score and stats and the same-date
    C-D stats (with a later fetched_at) changes nothing: values, NULLs and fingerprint."""
    g, same_day = games_on(league, G_DAY)
    boundary = as_of(G_DAY)
    f1 = compute_features(conn, g, boundary)
    assert f1.values["home_n_games_20"] == 20 and f1.values["league_total_100"] is not None
    assert f1.input_fingerprint["max_stats_fetched_at"] == "2026-10-23T12:00:00+00:00"
    later = datetime(2026, 11, 1, tzinfo=UTC)
    insert_extreme_games(conn, league, G_DAY + timedelta(days=1), 10)
    make_extreme(conn, [g], later)
    make_extreme(conn, [same_day], later)
    f2 = compute_features(conn, g, boundary)
    assert f2 == f1
    assert f2.input_fingerprint == f1.input_fingerprint


def test_t2_future_injection_leaves_every_earlier_day_unchanged(
    conn: Connection, league: League
) -> None:
    """T2: extreme scores and stats on every game from D(g) on (g included) leave the rows
    of every earlier date and of g itself identical; power check: one changed visible game
    changes g's Elo, rolling, season and league values."""
    days = [d for d in target_days(league, DEV) if d <= G_DAY]
    before = {d: compute_day(conn, d, as_of(d)) for d in days}
    future = (
        conn.execute(
            text(
                "SELECT game_id FROM games WHERE status_code = 3 AND "
                "(start_time_utc AT TIME ZONE 'America/New_York')::date >= :d"
            ),
            {"d": G_DAY},
        )
        .scalars()
        .all()
    )
    assert len(future) > 100
    make_extreme(conn, list(future), datetime(2026, 11, 1, tzinfo=UTC))
    after = {d: compute_day(conn, d, as_of(d)) for d in days}
    assert after == before
    g = games_on(league, G_DAY)[0]
    early = games_on(league, G_DAY - timedelta(days=2))[0]  # A-B two days before g
    make_extreme(conn, [early])
    changed = compute_features(conn, g, as_of(G_DAY))
    base = next(r for r in before[G_DAY] if r.game_id == g)
    assert changed != base
    for key in ("elo_home", "elo_prob", "league_total_100", "home_ortg_20", "home_ortg_std"):
        assert changed.values[key] != base.values[key], key


def test_t3_same_date_league_features_exclude_an_earlier_finished_game(
    conn: Connection, league: League
) -> None:
    """T3: C-D at 15:00 ET with total 300 is finished and past Delta at as_of 19:45 ET,
    but R1 keeps it out of league_total_100 and league_home_rate_std of the 20:00 ET A-B
    game (same values as with C-D at its usual time and as with C-D deleted)."""
    day = date(2024, 2, 15)
    ab, cd = games_on(league, day)
    boundary = et(day, 19, 45)
    conn.execute(
        text("UPDATE games SET start_time_utc = :s WHERE game_id = :g"), {"s": et(day, 20), "g": ab}
    )
    usual = compute_features(conn, ab, boundary)
    conn.execute(
        text(
            "UPDATE games SET start_time_utc = :s, home_score = 200, away_score = 100 "
            "WHERE game_id = :g"
        ),
        {"s": et(day, 15), "g": cd},
    )
    conn.execute(
        text(
            "UPDATE team_game_stats SET pts = CASE WHEN is_home THEN 200 ELSE 100 END "
            "WHERE game_id = :g"
        ),
        {"g": cd},
    )
    assert et(day, 15) + timing.DEFAULT_AVAILABILITY_LAG <= boundary  # R2 alone would allow it
    early = compute_features(conn, ab, boundary)
    conn.execute(text("DELETE FROM team_game_stats WHERE game_id = :g"), {"g": cd})
    conn.execute(text("DELETE FROM games WHERE game_id = :g"), {"g": cd})
    without = compute_features(conn, ab, boundary)
    assert early.values == usual.values == without.values
    assert early.input_fingerprint == without.input_fingerprint
    assert usual.values["league_total_100"] is not None


def test_t4_availability_lag_decides_the_late_game(
    conn: Connection, league: League, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T4: the 23:00 ET C-D game (04:00 UTC) counts at 07:00 UTC with Delta 3 h (equality)
    and not with Delta 4 h: C's counts, Elo and the league total differ; A's do not."""
    late = league.games[T4_LATE_GAME]
    boundary = as_of(date(2024, 1, 11))
    assert late["start_time_utc"] == datetime(2024, 1, 11, 4, 0, tzinfo=UTC)
    assert late["start_time_utc"] + timedelta(hours=3) == boundary
    a, _, c, _ = league.teams
    assert league.games[T4_NEXT_DAY_GAME]["home_team_id"] == a
    assert c in (late["home_team_id"], late["away_team_id"])
    monkeypatch.setitem(FEATURE_VERSIONS, "fv1-lag4", replace(SPEC, availability_lag_hours=4))
    with3 = compute_features(conn, T4_NEXT_DAY_GAME, boundary)
    with4 = compute_features(conn, T4_NEXT_DAY_GAME, boundary, version="fv1-lag4")
    assert with4.input_fingerprint["availability_lag_hours"] == 4
    k = with3.values["away_games_played"]
    assert with4.values["away_games_played"] == k - 1
    assert with3.input_fingerprint["input_completeness"]["away"] == {"with_stats": k, "finished": k}
    assert with4.input_fingerprint["input_completeness"]["away"] == {
        "with_stats": k - 1,
        "finished": k - 1,
    }
    for key in ("elo_away", "league_total_100", "away_ortg_5", "away_days_rest"):
        assert with3.values[key] != with4.values[key] or key == "away_days_rest", key
    assert with3.values["away_days_rest"] == with4.values["away_days_rest"] == 1  # schedule only
    home = {k: v for k, v in with3.values.items() if k.startswith("home_")}
    assert home == {k: v for k, v in with4.values.items() if k.startswith("home_")}
    assert with3.values["elo_home"] == with4.values["elo_home"]


def test_t5_a_finished_game_without_stats_is_excluded_and_counted(
    conn: Connection, league: League
) -> None:
    """T5: deleting the stat rows of A's last game keeps it in `finished`, drops it from
    `with_stats`, from the windows, from Elo and from the league total; one row left is
    the same as none."""
    g = games_on(league, G_DAY)[0]
    h = games_on(league, G_DAY - timedelta(days=2))[0]  # A-B two days earlier
    a = league.teams[0]
    assert league.games[g]["away_team_id"] == a and a in (
        league.games[h]["home_team_id"], league.games[h]["away_team_id"],
    )  # fmt: skip
    before = compute_features(conn, g, as_of(G_DAY))
    done = before.input_fingerprint["input_completeness"]
    conn.execute(text("DELETE FROM team_game_stats WHERE game_id = :g AND is_home"), {"g": h})
    one_row = compute_features(conn, g, as_of(G_DAY))
    conn.execute(text("DELETE FROM team_game_stats WHERE game_id = :g"), {"g": h})
    after = compute_features(conn, g, as_of(G_DAY))
    assert after == one_row
    k, n = done["away"]["with_stats"], done["away"]["finished"]
    assert after.input_fingerprint["input_completeness"]["away"] == {
        "with_stats": k - 1,
        "finished": n,
    }
    assert after.input_fingerprint["input_completeness"]["home"] == {
        "with_stats": k - 1,
        "finished": n,
    }
    assert after.input_fingerprint["n_prior_games_away"] == k - 1
    assert after.values["away_games_played"] == k - 1 and after.values["away_n_games_5"] == 5
    for key in ("away_ortg_5", "elo_away", "elo_home", "league_total_100"):
        assert after.values[key] != before.values[key], key
    assert after.values["away_days_rest"] == before.values["away_days_rest"]  # schedule


def test_t7_season_boundary_cold_windows_previous_season_and_regressed_elo(
    conn: Connection, league: League
) -> None:
    """T7: the 2024-25 opener has NULL windows and season values (n_games 0, games_played
    0, game_no 1, no rest values, season_day 0), prev_* from 2023-24 (checked by SQL),
    and Elo with the season break applied: equal to B2's p_home of that day."""
    opener = league.ids("2024-25")[0]
    day = date(2024, 10, 22)
    a, b, _, _ = league.teams
    assert (league.games[opener]["home_team_id"], league.games[opener]["away_team_id"]) == (a, b)
    row = compute_features(conn, opener, as_of(day))
    v = row.values
    for n in SPEC.windows:
        assert v[f"home_n_games_{n}"] == 0 and v[f"home_ortg_{n}"] is None
        assert v[f"away_n_games_{n}"] == 0 and v[f"diff_net_{n}"] is None
    assert (v["home_games_played"], v["home_game_no"], v["home_ortg_std"]) == (0, 1, None)
    assert (v["home_win_pct_std"], v["home_days_rest"], v["home_is_b2b"]) == (None, None, False)
    assert v["home_games_last_7d"] == 0 and v["season_day"] == 0
    assert row.input_fingerprint["input_completeness"]["home"] == {"with_stats": 0, "finished": 0}
    # prev_* by an independent SQL over A's 2023-24 games (predicted types).
    sums = conn.execute(
        text(
            """
            SELECT sum(s.pts) AS pts, sum(o.pts) AS opp,
                   sum((s.fga - s.oreb + s.tov + 0.44 * s.fta
                        + o.fga - o.oreb + o.tov + 0.44 * o.fta) / 2.0) AS poss,
                   count(*) AS n, sum(CASE WHEN s.pts > o.pts THEN 1 ELSE 0 END) AS wins
            FROM team_game_stats_primary s
            JOIN team_game_stats_primary o ON o.game_id = s.game_id AND o.team_id <> s.team_id
            JOIN games g ON g.game_id = s.game_id
            WHERE s.team_id = :team AND g.season = '2023-24'
              AND g.game_type IN ('regular', 'playin', 'playoffs') AND g.status_code = 3
            """
        ),
        {"team": a},
    ).one()
    assert v["home_prev_net"] == round(100 * float(sums.pts - sums.opp) / float(sums.poss), 6)
    assert v["home_prev_win_pct"] == round(sums.wins / sums.n, 6)
    assert v["home_prev_postseason"] is True and v["away_prev_postseason"] is True
    # Elo: the raw ratings of the visible results, then the pending break (D-063 item 2).
    history = load_history(conn, day, as_of(day), SPEC)
    results = [g for g in history.prior if g.visible]
    assert results[-1].season == "2023-24" and all(g.game_type != "cup_final" for g in results)
    ratings = elo_ratings(results, **SPEC.elo_constants)
    assert v["elo_home"] != round(ratings[a], 6)
    regress(ratings, carry=0.75, mean=1505.0)
    assert (v["elo_home"], v["elo_away"]) == (round(ratings[a], 6), round(ratings[b], 6))
    b2 = run_walk_forward(
        conn, EXPERIMENTS["B2"].make(), WalkForwardConfig(seasons=("2024-25",)), now=NOW
    )
    p = next(p for p in b2.predictions if p.game.game_id == opener)
    assert p.as_of == row.as_of
    assert round(p.p_home, 6) == v["elo_prob"]


def test_first_season_opener_is_fully_cold(conn: Connection, league: League) -> None:
    """The 2023-24 opener (no 2022-23 in the fixture): prev_* NULL, Elo 1500/1500 and the
    cold expectation 0.640065, league values NULL, home_adv 1."""
    opener = league.ids("2023-24")[0]
    v = compute_features(conn, opener, as_of(date(2023, 10, 24))).values
    assert (v["home_prev_net"], v["home_prev_win_pct"], v["home_prev_postseason"]) == (None,) * 3
    assert (v["elo_home"], v["elo_away"], v["elo_diff_adj"]) == (1500.0, 1500.0, 100.0)
    assert (
        v["elo_prob"] == round(expected_home(1500, 1500, hca=100, is_neutral=False), 6) == 0.640065
    )
    assert (v["league_total_100"], v["league_home_rate_std"], v["home_adv"]) == (None, None, 1)


def test_elo_prob_equals_b2_p_home_on_every_prediction_day(
    conn: Connection, league: League
) -> None:
    """The runner's R1/R2 and ours agree game by game: elo_prob == B2's p_home (6 places)
    for every 2023-24 and 2024-25 prediction, Cup final never predicted."""
    b2 = run_walk_forward(conn, EXPERIMENTS["B2"].make(), WalkForwardConfig(seasons=DEV), now=NOW)
    assert len(b2.predictions) > 300
    by_day: dict[date, list[Any]] = {}
    for p in b2.predictions:
        by_day.setdefault(p.game.et_date, []).append(p)
    for day, preds in by_day.items():
        rows = {r.game_id: r for r in compute_day(conn, day, as_of(day))}
        assert set(rows) == {p.game.game_id for p in preds}, day
        for p in preds:
            assert rows[p.game.game_id].values["elo_prob"] == round(p.p_home, 6), p.game.game_id
            assert rows[p.game.game_id].as_of == p.as_of
    assert CUP_FINAL not in {p.game.game_id for p in b2.predictions}


def test_compute_day_equals_compute_features_for_every_target_game(
    conn: Connection, league: League
) -> None:
    """(g) Row by row, every key, both seasons: the once-loaded history gives the same
    values and fingerprint as the single-game path; (h) a second run is identical."""
    n = 0
    for day in target_days(league, DEV):
        rows = compute_day(conn, day, as_of(day))
        assert rows and list(rows[0].values) == list(SPEC.keys)
        for row in rows:
            single = compute_features(conn, row.game_id, as_of(day))
            assert single == row, row.game_id
            n += 1
        assert compute_day(conn, day, as_of(day)) == rows  # T12 determinism
    assert n == sum(
        1 for g in league.games.values()
        if g["season"] in DEV and game_type(g["game_id"]) in PREDICTED_GAME_TYPES
    )  # fmt: skip


def test_cup_final_feeds_rest_but_never_stats_or_elo(conn: Connection, league: League) -> None:
    """D-049 M-3: A's game after the 2023-12-10 Cup final (which has stat rows in the
    fixture) sees days_rest 1 from it, while removing the final changes no stat, Elo or
    league value."""
    day = date(2023, 12, 11)
    g = games_on(league, day)[0]
    a = league.teams[0]
    assert league.games[g]["home_team_id"] == a and league.games[CUP_FINAL]["home_team_id"] == a
    with_final = compute_features(conn, g, as_of(day))
    assert (with_final.values["home_days_rest"], with_final.values["home_is_b2b"]) == (1, True)
    conn.execute(text("DELETE FROM team_game_stats WHERE game_id = :g"), {"g": CUP_FINAL})
    conn.execute(text("DELETE FROM games WHERE game_id = :g"), {"g": CUP_FINAL})
    without = compute_features(conn, g, as_of(day))
    assert (without.values["home_days_rest"], without.values["home_is_b2b"]) == (2, False)
    assert without.values["home_games_last_7d"] == with_final.values["home_games_last_7d"] - 1
    differing = {k for k in with_final.values if with_final.values[k] != without.values[k]}
    assert differing == {"home_days_rest", "home_is_b2b", "home_games_last_7d", "diff_days_rest",
                         "diff_games_last_7d"}  # fmt: skip
    assert without.input_fingerprint == with_final.input_fingerprint


def test_stats_fetched_at_of_visible_rows_only(conn: Connection, league: League) -> None:
    """max_stats_fetched_at follows the newest visible stat row and ignores a newer row of a
    game that is not visible (same date), in UTC ISO form."""
    g, same_day = games_on(league, G_DAY)
    h = games_on(league, G_DAY - timedelta(days=2))[0]
    base = compute_features(conn, g, as_of(G_DAY)).input_fingerprint["max_stats_fetched_at"]
    conn.execute(
        text("UPDATE team_game_stats SET fetched_at = :t WHERE game_id = :g"),
        {"t": datetime(2026, 11, 2, 3, 4, 5, tzinfo=UTC), "g": same_day},
    )
    assert compute_features(conn, g, as_of(G_DAY)).input_fingerprint["max_stats_fetched_at"] == base
    conn.execute(
        text("UPDATE team_game_stats SET fetched_at = :t WHERE game_id = :g"),
        {"t": datetime(2026, 11, 2, 3, 4, 5, tzinfo=UTC), "g": h},
    )
    newer_fp = compute_features(conn, g, as_of(G_DAY)).input_fingerprint
    newer = newer_fp["max_stats_fetched_at"]
    assert newer == "2026-11-02T03:04:05+00:00"
    # A finished game with one stat row left is not visible (R2): its row's fetched_at, even
    # newer than everything else, never enters the fingerprint (review mutation M23).
    h2 = games_on(league, G_DAY - timedelta(days=4))[0]
    conn.execute(text("DELETE FROM team_game_stats WHERE game_id = :g AND is_home"), {"g": h2})
    conn.execute(
        text("UPDATE team_game_stats SET fetched_at = :t WHERE game_id = :g"),
        {"t": datetime(2026, 12, 31, tzinfo=UTC), "g": h2},
    )
    one_row = compute_features(conn, g, as_of(G_DAY)).input_fingerprint
    assert one_row["max_stats_fetched_at"] == newer
    assert one_row["n_prior_games_away"] == newer_fp["n_prior_games_away"] - 1  # T5 again


def test_fingerprint_shape(conn: Connection, league: League) -> None:
    """Criterion 3: the fingerprint keys and the Elo constants; no odds key (F-11 is fv2)."""
    fp = compute_features(conn, games_on(league, G_DAY)[0], as_of(G_DAY)).input_fingerprint
    assert set(fp) == {
        "feature_version", "poss_formula", "availability_lag_hours", "max_stats_fetched_at",
        "n_prior_games_home", "n_prior_games_away", "input_completeness", "elo",
    }  # fmt: skip
    assert (fp["feature_version"], fp["poss_formula"], fp["availability_lag_hours"]) == (
        "fv1",
        "d035",
        3,
    )
    assert fp["elo"] == {"k": 20.0, "hca": 100.0, "carry": 0.75, "mean": 1505.0, "initial": 1500.0}
    assert fp["n_prior_games_home"] == fp["input_completeness"]["home"]["with_stats"]
    assert not any("odds" in key for key in fp)


def test_live_as_of_in_the_day_sees_the_same_history(conn: Connection, league: League) -> None:
    """A mid-day as_of (live) sees the same rows as 07:00 UTC when nothing finished in
    between (R1 keeps the day's own games out), and the row carries that as_of."""
    g = games_on(league, G_DAY)[0]
    noon = et(G_DAY, 12, 0)
    morning, live = compute_features(conn, g, as_of(G_DAY)), compute_features(conn, g, noon)
    assert live.values == morning.values and live.as_of == noon
    assert live.start_time_utc == league.games[g]["start_time_utc"]


def test_unknown_game_tbd_teams_and_naive_as_of_are_errors(
    conn: Connection, league: League
) -> None:
    """compute_features refuses an unknown game and a game without teams; a naive as_of is
    a ValueError; compute_day leaves TBD games out."""
    with pytest.raises(FeatureComputeError, match="unknown game"):
        compute_features(conn, "0099999999", as_of(G_DAY))
    with pytest.raises(ValueError, match="aware"):
        compute_features(conn, games_on(league, G_DAY)[0], datetime(2023, 12, 13, 7, 0))
    g, same_day = games_on(league, G_DAY)
    conn.execute(
        text(
            "UPDATE games SET home_team_id = NULL, away_team_id = NULL, status_code = 1, "
            "home_score = NULL, away_score = NULL WHERE game_id = :g"
        ),
        {"g": same_day},
    )
    with pytest.raises(FeatureComputeError, match="no teams"):
        compute_features(conn, same_day, as_of(G_DAY))
    conn.execute(text("UPDATE games SET is_time_tbd = true WHERE game_id = :g"), {"g": g})
    assert compute_day(conn, G_DAY, as_of(G_DAY)) == []
    assert isinstance(compute_features(conn, g, as_of(G_DAY)), FeatureRow)
