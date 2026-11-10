"""`presagio backtest compare` on the temp league DB (P-037 A1, A6)."""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pytest
from sqlalchemy import Connection, Engine, event, text

from presagio import cli
from presagio.evaluation import cli as backtest_cli
from presagio.evaluation.compare import common_set, run_arms
from presagio.evaluation.experiments import EXPERIMENTS, Experiment, resolve_experiment
from presagio.evaluation.walkforward import LeakageGuardError, WalkForwardConfig, run_walk_forward
from presagio.models.baselines import AlwaysHomeBaseline, SeasonWinRateBaseline

from .league import LIVE_AVAILABLE, LIVE_PRED_ONLY, LIVE_UNMATCHED, League

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
TABLES = ("games", "odds_snapshots", "model_versions", "predictions", "evaluations")


def compare(engine: Engine, *args: str) -> int:
    """Run `presagio backtest compare ...` against the league DB."""
    return cli.main(["backtest", "compare", *args], engine=engine, now=NOW, setup_logging=False)


def cells(out: str, season: str, phase: str, experiment: str) -> list[str]:
    """The cells of one metrics row."""
    prefix = f"| {season} | {phase} | {experiment} |"
    lines = [line for line in out.splitlines() if line.startswith(prefix)]
    assert len(lines) == 1, (prefix, lines)
    return [c.strip() for c in lines[0].strip().strip("|").split("|")]


def arm_cells(out: str, experiment: str) -> list[str]:
    """The cells of one experiment's row in the 'Experiments and the common set' table."""
    table = out.split("## Experiments and the common set")[1].split("##")[0]
    lines = [line for line in table.splitlines() if line.startswith(f"| {experiment} |")]
    assert len(lines) == 1
    return [c.strip() for c in lines[0].strip().strip("|").split("|")]


def counts(engine: Engine) -> dict[str, int]:
    """Row counts of a few tables (compare must leave them as they were)."""
    with engine.connect() as conn:
        return {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one() for t in TABLES}


class SkipOneGame:
    """B0 that returns nothing for one game (to test the intersection)."""

    name = "B0 without one game (test)"
    trains = False
    feature_version = None
    uses_results = True
    skipped = "0022400021"

    def __init__(self) -> None:
        """Wrap a fresh B0."""
        self._inner = AlwaysHomeBaseline()

    def fit(self, training: Any) -> None:
        """Nothing to learn."""

    def predict(self, as_of: datetime, games: Sequence[Any], context: Any) -> Mapping[str, Any]:
        """B0's predictions minus the skipped game."""
        out = dict(self._inner.predict(as_of, games, context))
        out.pop(self.skipped, None)
        return out


def test_common_set_is_the_intersection_and_the_header_counts_what_left(
    engine: Engine, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A game one experiment did not predict leaves the common set of every experiment; the
    header counts it per experiment with the reason. Nothing is written."""
    monkeypatch.setitem(EXPERIMENTS, "H-98", Experiment("H-98", "B0 without one game", SkipOneGame))
    before = counts(engine)
    code = compare(
        engine, "--experiments", "B0,B1,H-98", "--reference", "B0", "--seasons", "2024-25"
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "Common game set: (game_id, as_of) reported, final and scored" in out
    assert "n = 170." in out  # 171 games of 2024-25 minus the one H-98 skipped
    # experiment, model, version, predictions, not predicted, warm-up, not final, no p_home,
    # eligible, outside, why
    assert arm_cells(out, "B0")[3:] == ["171", "none", "0", "0", "0", "171", "1", "not in H-98 1"]
    assert arm_cells(out, "B1")[8:] == ["171", "1", "not in H-98 1"]
    assert arm_cells(out, "H-98")[3:] == [
        "170",
        "not_predicted 1 (by market_status: no_odds_season 1)",
        "0",
        "0",
        "0",
        "170",
        "0",
        "-",
    ]
    assert cells(out, "2024-25", "regular", "B0")[3] == "169"
    assert cells(out, "2024-25", "regular", "H-98")[3] == "169"
    assert cells(out, "2024-25", "all", "B1")[3] == "170"
    assert counts(engine) == before


def test_same_seed_gives_the_same_report_and_another_seed_moves_the_intervals(
    engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """Determinism: the same command twice prints the same bytes; the seed is in the header."""
    args = ("--experiments", "B0,B1,B2", "--reference", "B0", "--seasons", "2023-24,2024-25")
    assert compare(engine, *args, "--bootstrap", "200", "--seed", "3") == 0
    first = capsys.readouterr().out
    assert compare(engine, *args, "--bootstrap", "200", "--seed", "3") == 0
    second = capsys.readouterr().out
    assert first == second
    assert "--bootstrap 200 --seed 3`" in first and "200 resamples, seed 3," in first
    assert compare(engine, *args, "--bootstrap", "200", "--seed", "4") == 0
    other = capsys.readouterr().out
    assert other != first
    point = cells(first, "all", "all", "B2")[5].split(" [")[0]
    assert cells(other, "all", "all", "B2")[5].split(" [")[0] == point  # estimates unchanged


def test_hand_computed_paired_delta(
    engine: Engine, conn: Connection, league: League, capsys: pytest.CaptureFixture[str]
) -> None:
    """One delta by hand: B1 - B0 on 2024-25 regular games. Per-game log losses from the
    walk-forward p_home and the games table; the interval resamples the same game indices
    for both columns (numpy default_rng(seed).integers, 5th..95th percentile)."""
    n_boot, seed = 300, 7
    assert (
        compare(
            engine,
            "--experiments",
            "B0,B1",
            "--reference",
            "B0",
            "--seasons",
            "2024-25",
            "--bootstrap",
            str(n_boot),
            "--seed",
            str(seed),
        )
        == 0
    )
    out = capsys.readouterr().out
    config = WalkForwardConfig(seasons=("2024-25",))
    p: dict[str, dict[str, float]] = {}
    for name, predictor in (("B0", AlwaysHomeBaseline()), ("B1", SeasonWinRateBaseline())):
        run = run_walk_forward(conn, predictor, config, now=NOW)
        p[name] = {r.game.game_id: r.p_home for r in run.predictions}  # type: ignore[misc]
    games = sorted(
        (
            g
            for g in league.games.values()
            if g["season"] == "2024-25" and g["game_id"][:3] == "002"
        ),
        key=lambda g: (g["start_time_utc"], g["game_id"]),
    )
    y = np.array([int(g["home_score"] > g["away_score"]) for g in games])

    def log_losses(name: str) -> np.ndarray:
        """Per-game log loss of one experiment, start order."""
        ps = np.array([p[name][g["game_id"]] for g in games])
        return -(y * np.log(ps) + (1 - y) * np.log(1 - ps))

    b0, b1 = log_losses("B0"), log_losses("B1")
    n = len(games)
    assert n == 170
    index = np.random.default_rng(seed).integers(0, n, size=(n_boot, n))
    deltas = b1[index].mean(axis=1) - b0[index].mean(axis=1)
    lower, upper = np.percentile(deltas, [5, 95])
    estimate = b1.mean() - b0.mean()
    row = cells(out, "2024-25", "regular", "B1")
    assert row[8] == f"{estimate:+.4f} [{lower:+.4f}, {upper:+.4f}]"
    # The single-experiment interval uses the same resampled games.
    lo1, hi1 = np.percentile(b1[index].mean(axis=1), [5, 95])
    assert row[5] == f"{b1.mean():.4f} [{lo1:.4f}, {hi1:.4f}]"
    accuracy = {
        name: sum((p[name][g["game_id"]] >= 0.5) == bool(t) for g, t in zip(games, y, strict=True))
        / n
        for name in ("B0", "B1")
    }
    assert row[4] == f"{accuracy['B1']:.3f}"
    assert row[10] == f"{accuracy['B1'] - accuracy['B0']:+.3f}"
    assert math.isclose(float(row[8].split(" [")[0]), estimate, abs_tol=5e-5)


def test_reference_delta_is_zero_and_always_home_is_the_home_win_rate(
    engine: Engine, league: League, capsys: pytest.CaptureFixture[str]
) -> None:
    """The reference's own deltas are 0 with a [0, 0] interval; the always-home row is the
    share of home wins of the segment (D-055 item 6)."""
    assert (
        compare(engine, "--experiments", "B1,B2", "--reference", "B2", "--seasons", "2024-25") == 0
    )
    out = capsys.readouterr().out
    row = cells(out, "2024-25", "regular", "B2")
    assert row[8:11] == ["+0.0000 [+0.0000, +0.0000]", "+0.0000 [+0.0000, +0.0000]", "+0.000"]
    regular = [
        g for g in league.games.values() if g["season"] == "2024-25" and g["game_id"][:3] == "002"
    ]
    home_rate = sum(g["home_score"] > g["away_score"] for g in regular) / len(regular)
    home = cells(out, "2024-25", "regular", "always home (= home win rate)")
    assert home[3:5] == ["170", f"{home_rate:.3f}"] and home[5:11] == ["-"] * 6
    # 2024-25 has both phases: a season total; one season: no all-seasons row.
    assert cells(out, "2024-25", "all", "B1")[3] == "171"
    assert "| all | all |" not in out


def test_market_experiment_limits_the_set_to_games_with_a_market(
    engine: Engine, conn: Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    """With B3 the common set is the games with a prediction-time market (available +
    pred_only): the unmatched-event game B2 predicted is left out and counted; n < 100 is
    reference only."""
    assert (
        compare(engine, "--experiments", "B2,B3", "--reference", "B2", "--seasons", "2026-27") == 0
    )
    out = capsys.readouterr().out
    assert "market_status available or pred_only because a market experiment takes part" in out
    assert "n = 2." in out
    assert arm_cells(out, "B2")[3:] == ["4", "none", "0", "1", "0", "3", "1", "not in B3 1"]
    b3 = arm_cells(out, "B3")
    assert b3[4].startswith("not_predicted 2 (by market_status: ")
    b2_row = cells(out, "2026-27", "regular", "B2")
    assert b2_row[3] == "2" and b2_row[11] == "reference only (n < 100)"
    # The two common games are the available and pred_only ones.
    arms = run_arms(
        conn,
        [resolve_experiment("B2"), resolve_experiment("B3")],
        WalkForwardConfig(seasons=("2026-27",)),
        now=NOW,
    )
    common = common_set(arms)
    assert {key[0] for key in common.keys} == {LIVE_AVAILABLE, LIVE_PRED_ONLY}
    b2_games, b3_games = ({key[0] for key in arm} for arm in common.by_arm)
    assert LIVE_UNMATCHED in b2_games and LIVE_UNMATCHED not in b3_games


def test_a_season_without_market_has_no_common_set_with_b3(
    engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """B3 predicts nothing in 2023-24 (no odds): the report says so, exit 0."""
    assert (
        compare(engine, "--experiments", "B2,B3", "--reference", "B2", "--seasons", "2023-24") == 0
    )
    out = capsys.readouterr().out
    assert "n = 0." in out and out.rstrip().endswith("No common scored predictions.")


def test_games_are_loaded_once_and_reads_are_shared_between_experiments(
    engine: Engine, conn: Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    """Three experiments cost the database what one costs: one games load, one quote read
    per prediction day."""
    statements: list[str] = []

    def record(_conn: Any, _cursor: Any, statement: str, *_: Any) -> None:
        """Keep every SQL text sent."""
        statements.append(statement)

    def run(experiments: str) -> list[str]:
        """Statements of one compare."""
        statements.clear()
        assert (
            compare(
                engine, "--experiments", experiments, "--reference", "B0", "--seasons", "2023-24"
            )
            == 0
        )
        capsys.readouterr()
        return list(statements)

    event.listen(engine, "before_cursor_execute", record)
    try:
        two = run("B0,B1")
        three = run("B0,B1,B2")
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert len(two) == len(three)
    assert sum("FROM team_game_stats_primary" in s for s in three) == 1
    days = run_walk_forward(
        conn, AlwaysHomeBaseline(), WalkForwardConfig(seasons=("2023-24",)), now=NOW
    ).days
    assert sum("FROM odds_snapshots s" in s for s in three) == days == 89


def test_compare_runs_in_one_repeatable_read_read_only_transaction(
    engine: Engine, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The experiments run in one REPEATABLE READ, READ ONLY snapshot: a write attempt fails
    (exit 1, the server's message) and nothing changes."""
    seen: dict[str, str] = {}

    def write_attempt(conn: Connection, *args: object, **kwargs: object) -> None:
        """Record the transaction mode, then pretend a predictor tried to write."""
        seen["isolation"] = conn.execute(text("SHOW transaction_isolation")).scalar_one()
        seen["read_only"] = conn.execute(text("SHOW transaction_read_only")).scalar_one()
        conn.execute(text("UPDATE games SET status_text = status_text WHERE false"))

    monkeypatch.setattr(backtest_cli, "run_arms", write_attempt)
    before = counts(engine)
    code = compare(engine, "--experiments", "B0,B1", "--reference", "B0", "--seasons", "2023-24")
    captured = capsys.readouterr()
    assert seen == {"isolation": "repeatable read", "read_only": "on"}
    assert code == 1 and "read-only transaction" in captured.err and captured.out == ""
    assert counts(engine) == before


def test_leakage_guard_exits_3(
    engine: Engine,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A LeakageGuardError inside compare is exit 3 with one stderr line and an ERROR log."""

    def explode(*args: object, **kwargs: object) -> None:
        """Pretend a predictor broke a time rule."""
        raise LeakageGuardError("0022300001: train_cutoff/as_of/tip-off order")

    monkeypatch.setattr(backtest_cli, "run_arms", explode)
    with caplog.at_level(logging.ERROR, logger="presagio.evaluation"):
        code = compare(
            engine, "--experiments", "B0,B1", "--reference", "B0", "--seasons", "2023-24"
        )
    captured = capsys.readouterr()
    assert code == 3 and captured.err.startswith("presagio backtest: leakage guard: ")
    assert captured.out == "" and "Traceback" not in captured.err
    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_unreachable_database_is_one_line_and_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    """A database error is one line, exit 1, no traceback."""
    from presagio.db.session import make_engine

    dead = make_engine("postgresql+psycopg://x:y@127.0.0.1:9/none", connect_timeout=1)
    code = compare(dead, "--experiments", "B0,B1", "--reference", "B0", "--seasons", "2023-24")
    err = capsys.readouterr().err
    assert code == 1 and err.startswith("presagio backtest: ") and "Traceback" not in err
