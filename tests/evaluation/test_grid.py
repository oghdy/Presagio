"""`presagio backtest grid` on the temp league DB (P-037 A3, A6)."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, event, text

from presagio import cli
from presagio.evaluation import grid as grid_module
from presagio.evaluation.grid import Cell, best_cell
from presagio.evaluation.walkforward import WalkForwardConfig, run_walk_forward
from presagio.models.elo import EloBaseline

from .league import League

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
TABLES = ("games", "model_versions", "predictions", "evaluations")


def grid(engine: Engine, *args: str) -> int:
    """Run `presagio backtest grid --experiment B2 ...` against the league DB."""
    return cli.main(
        ["backtest", "grid", "--experiment", "B2", *args],
        engine=engine,
        now=NOW,
        setup_logging=False,
    )


def cell_rows(out: str) -> list[list[str]]:
    """Rows of the cells table: rank, k, hca, carry, n, log loss, Brier, accuracy, dLL,
    distance, note."""
    table = out.split("## Cells (log loss ascending)")[1].split("## Best cell")[0]
    rows = [
        line
        for line in table.splitlines()
        if line.startswith("| ") and "rank" not in line and "---" not in line
    ]
    return [[c.strip() for c in line.strip().strip("|").split("|")] for line in rows]


def counts(engine: Engine) -> dict[str, int]:
    """Row counts (grid must write nothing)."""
    with engine.connect() as conn:
        return {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one() for t in TABLES}


def test_cells_are_the_product_and_the_best_cell_is_the_lowest_log_loss(
    engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """A3: 3 x 2 x 2 = 12 cells, log loss ascending; the best is rank 1 and the start cell
    has dLL 0; the next command names the best configuration and the next season."""
    before = counts(engine)
    code = grid(
        engine,
        "--param",
        "k=10,20,30",
        "--param",
        "hca=50,100",
        "--param",
        "carry=0.75,0.9",
        "--select-season",
        "2023-24",
    )
    out = capsys.readouterr().out
    assert code == 0
    rows = cell_rows(out)
    assert len(rows) == 12 and "-> 12 cells." in out
    assert [r[0] for r in rows] == [str(i) for i in range(1, 13)]
    losses = [float(r[5]) for r in rows]
    assert losses == sorted(losses)
    assert {(r[1], r[2], r[3]) for r in rows} == {
        (k, h, c) for k in ("10", "20", "30") for h in ("50", "100") for c in ("0.75", "0.9")
    }
    assert "best" in rows[0][10]
    start = next(r for r in rows if "start" in r[10])
    assert (start[1], start[2], start[3], start[8]) == ("20", "100", "0.75", "+0.0000")
    assert all(r[4] == "104" for r in rows)  # reported games of 2023-24 (from 2024-01-01)
    best = rows[0]
    best_id = f"B2[k={best[1]},hca={best[2]},carry={best[3]}]"
    assert (
        f'`uv run presagio backtest compare --experiments "B2,{best_id}" --reference B2 '
        "--seasons 2024-25`"
    ) in out
    assert counts(engine) == before


def test_cell_metrics_match_a_single_run_of_that_configuration(
    engine: Engine, conn: Connection, league: League, capsys: pytest.CaptureFixture[str]
) -> None:
    """A cell is exactly `B2[k,hca,carry]` over the selection window: log loss, Brier and
    accuracy recomputed by hand from a separate walk-forward of that configuration."""
    assert (
        grid(
            engine,
            "--param",
            "k=15,20",
            "--select-season",
            "2023-24",
            "--select-from",
            "2024-02-01",
        )
        == 0
    )
    out = capsys.readouterr().out
    row = next(r for r in cell_rows(out) if r[1] == "15")
    run = run_walk_forward(
        conn, EloBaseline(k=15), WalkForwardConfig(seasons=("2023-24",)), now=NOW
    )
    chosen = [p for p in run.predictions if p.game.et_date >= date(2024, 2, 1)]
    ys = [
        int(league.games[p.game.game_id]["home_score"] > league.games[p.game.game_id]["away_score"])
        for p in chosen
    ]
    ps = [p.p_home for p in chosen]
    n = len(chosen)
    ll = -sum(y * math.log(p) + (1 - y) * math.log(1 - p) for p, y in zip(ps, ys, strict=True)) / n  # type: ignore[arg-type]
    brier = sum((p - y) ** 2 for p, y in zip(ps, ys, strict=True)) / n  # type: ignore[operator]
    acc = sum((p >= 0.5) == bool(y) for p, y in zip(ps, ys, strict=True)) / n  # type: ignore[operator]
    assert row[4:8] == [str(n), f"{ll:.4f}", f"{brier:.4f}", f"{acc:.3f}"]


def test_ties_go_to_the_start_then_to_grid_order() -> None:
    """Equal log loss: the cell nearest the start wins; equal distance too: the first cell
    in grid order. Differences within float noise are ties."""

    def cell(index: int, k: float, hca: float, carry: float, ll: float) -> Cell:
        """A cell with only the fields the rule reads."""
        return Cell(index, {"k": k, "hca": hca, "carry": carry}, 10, ll, 0.25, 0.5)

    far, near = cell(0, 30, 50, 0.75, 0.60), cell(1, 25, 100, 0.75, 0.60 + 1e-15)
    assert best_cell([far, near]) is near  # distance 1 beats 4
    first, second = cell(0, 15, 100, 0.75, 0.60), cell(1, 25, 100, 0.75, 0.60)
    assert best_cell([first, second]) is first  # same distance: grid order (index)
    assert best_cell([second, first]) is first  # the grid index decides, not list order
    lower = cell(2, 30, 125, 0.9, 0.59)
    assert best_cell([first, second, lower]) is lower  # a real difference is not a tie


def test_carry_grid_ties_are_broken_by_the_start_value(
    engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """The league has no season before 2023-24, so carry changes nothing there: all three
    cells tie and 0.75 (distance 0) wins although 0.9 comes first in grid order."""
    code = grid(
        engine,
        "--param",
        "carry=0.9,0.6,0.75",
        "--fixed",
        "k=25,hca=75",
        "--select-season",
        "2023-24",
        "--select-from",
        "2023-10-24",
    )
    out = capsys.readouterr().out
    assert code == 0
    rows = cell_rows(out)
    assert len({r[5] for r in rows}) == 1
    assert [r[3] for r in rows] == ["0.9", "0.6", "0.75"]  # ties keep grid order in the table
    best = next(r for r in rows if "best" in r[10])
    assert (best[1], best[2], best[3]) == ("25", "75", "0.75") and "start" in best[10]
    assert all(r[4] == "174" for r in rows)  # every scored 2023-24 game from the opener
    assert "Start cell: k=25,hca=75,carry=0.75" in out
    assert "(the best cell is the start cell)." in out
    assert '--experiments "B2,B2[k=25,hca=75,carry=0.75]" --reference B2' in out


def test_no_metric_of_another_season_is_computed_or_printed(
    engine: Engine, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nested tuning (6-4): every cell's walk-forward is configured with the selection season
    only, and the output names another season only in the next command line."""
    seen: list[tuple[str, ...]] = []
    original = grid_module.run_walk_forward

    def spy(conn: Any, predictor: Any, config: WalkForwardConfig, **kwargs: Any) -> Any:
        """Record the seasons every cell asks for."""
        seen.append(config.seasons)
        result = original(conn, predictor, config, **kwargs)
        assert {p.game.season for p in result.predictions} == {"2023-24"}
        return result

    monkeypatch.setattr(grid_module, "run_walk_forward", spy)
    code = grid(
        engine,
        "--param",
        "k=10,20",
        "--param",
        "hca=75,100",
        "--select-season",
        "2023-24",
    )
    out = capsys.readouterr().out
    assert code == 0 and seen == [("2023-24",)] * 4
    other = [line for line in out.splitlines() if "2024-25" in line]
    assert len(other) == 1 and other[0].startswith("- Next (the one pre-registered comparison")
    for season in ("2022-23", "2025-26", "2026-27"):
        assert season not in out


def test_games_and_odds_are_read_once_per_run_not_per_cell(
    engine: Engine, conn: Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    """A3: one cell and four cells send the same statements: one games load, one quote read
    per prediction day, one closing read; cells after the first reuse the rows."""
    statements: list[str] = []

    def record(_conn: Any, _cursor: Any, statement: str, *_: Any) -> None:
        """Keep every SQL text sent."""
        statements.append(statement)

    def run(*params: str) -> list[str]:
        """Statements of one grid."""
        statements.clear()
        assert grid(engine, *params, "--select-season", "2023-24") == 0
        capsys.readouterr()
        return list(statements)

    event.listen(engine, "before_cursor_execute", record)
    try:
        one = run("--param", "k=20")
        four = run("--param", "k=10,20", "--param", "hca=75,100")
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert one == four
    days = run_walk_forward(
        conn, EloBaseline(), WalkForwardConfig(seasons=("2023-24",)), now=NOW
    ).days
    assert sum("FROM team_game_stats_primary" in s for s in four) == 1
    assert sum("FROM odds_snapshots s" in s for s in four) == days == 89
    assert sum("FROM odds_closing_lines" in s for s in four) == 1


def test_grid_runs_in_one_repeatable_read_read_only_transaction(
    engine: Engine, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Like compare: one REPEATABLE READ, READ ONLY snapshot; a write fails with exit 1."""
    seen: dict[str, str] = {}

    def write_attempt(conn: Connection, *args: object, **kwargs: object) -> None:
        """Record the transaction mode, then try to write."""
        seen["isolation"] = conn.execute(text("SHOW transaction_isolation")).scalar_one()
        seen["read_only"] = conn.execute(text("SHOW transaction_read_only")).scalar_one()
        conn.execute(text("UPDATE games SET status_text = status_text WHERE false"))

    from presagio.evaluation import cli as backtest_cli

    monkeypatch.setattr(backtest_cli, "run_grid", write_attempt)
    code = grid(engine, "--param", "k=20", "--select-season", "2023-24")
    captured = capsys.readouterr()
    assert seen == {"isolation": "repeatable read", "read_only": "on"}
    assert code == 1 and "read-only transaction" in captured.err and captured.out == ""


def test_an_empty_selection_window_exits_1(
    engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """--select-from after the season's last game: the report says so and the exit is 1."""
    code = grid(
        engine, "--param", "k=20", "--select-season", "2023-24", "--select-from", "2024-09-01"
    )
    captured = capsys.readouterr()
    assert code == 1 and "No scored game in the selection window." in captured.out
    assert "no scored game" in captured.err
