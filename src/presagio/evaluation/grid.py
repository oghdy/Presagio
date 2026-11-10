"""`presagio backtest grid` (P-037 A3): Elo constant grid on one selection season (H-04(a)).

Nested tuning (modeling design v1.2 6-4): the constants are chosen on the selection season
only and reported on the next season with one pre-registered comparison. So the walk-forward
of every cell is configured with the selection season alone: no game of another season is
predicted or scored, and no metric of another season can be printed (earlier seasons still
feed the Elo state as results before as_of, D-055 item 1).

Each cell runs exactly what `backtest run --experiment "B2[k=..,hca=..,carry=..]"` runs (same
runner, same scoring), inside one REPEATABLE READ, READ ONLY transaction. The games are loaded
once per run and every other read goes through CachedReads, so cells after the first reuse
the rows of the first: the database cost does not grow with the number of cells.

Selection window: by default the reported games (from 2024-01-01, the baseline warm-up rule);
with --select-from D every scored game of the season whose ET date is >= D (the carry search
starts at the season opener, where the 2022-23 -> 2023-24 break acts; PM decision P-037).

Table: one row per cell, log loss ascending, n / log loss / Brier / accuracy, no bootstrap.
Best cell: the lowest log loss; ties (|difference| <= TIE_TOLERANCE, float summation noise)
go to the cell nearest the starting values, |k - 20| / 5 + |hca - 100| / 25 +
|carry - 0.75| / 0.15, then to the first cell in grid order (the order of --param values,
the first --param varying slowest).
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import Connection

from presagio.evaluation.compare import CachedReads
from presagio.evaluation.data import load_games
from presagio.evaluation.experiments import (
    ELO_EXPERIMENT,
    ELO_PARAMS,
    ELO_START,
    format_params,
    resolve_experiment,
)
from presagio.evaluation.holdout import HOLDOUT_SEASONS
from presagio.evaluation.report import _fmt, _table
from presagio.evaluation.scoring import METRIC_VERSION, EvaluationRecord, score_run
from presagio.evaluation.walkforward import WalkForwardConfig, run_walk_forward

# Two log losses closer than this are a tie (float summation noise, not a difference).
TIE_TOLERANCE = 1e-12
# Scale of one "step" per constant in the tie-break distance (P-037 A3).
START_SCALE: Mapping[str, float] = {"k": 5.0, "hca": 25.0, "carry": 0.15}


@dataclass(frozen=True)
class Cell:
    """One grid cell's configuration and its selection-window metrics."""

    index: int  # position in grid order
    params: Mapping[str, float]  # k, hca, carry
    n: int
    log_loss: float | None
    brier: float | None
    accuracy: float | None

    @property
    def experiment_id(self) -> str:
        """The parameterised experiment id of this configuration."""
        return f"{ELO_EXPERIMENT}[{format_params(self.params)}]"

    @property
    def distance(self) -> float:
        """Tie-break distance to the D-049 M-7 starting values."""
        return sum(abs(self.params[k] - ELO_START[k]) / START_SCALE[k] for k in ELO_PARAMS)


def grid_cells(
    grid: Sequence[tuple[str, Sequence[float]]], fixed: Mapping[str, float]
) -> list[dict[str, float]]:
    """Every configuration in grid order: starting values, then fixed values, then the cell."""
    keys = [key for key, _ in grid]
    out = []
    for values in itertools.product(*(vals for _, vals in grid)):
        out.append({**ELO_START, **fixed, **dict(zip(keys, values, strict=True))})
    return out


def start_cell(
    grid: Sequence[tuple[str, Sequence[float]]], fixed: Mapping[str, float]
) -> dict[str, float]:
    """The configuration of the starting values for the searched constants (fixed as given)."""
    return {**ELO_START, **fixed, **{key: ELO_START[key] for key, _ in grid}}


def selected(
    records: Sequence[EvaluationRecord], season: str, select_from: date | None
) -> list[EvaluationRecord]:
    """Scored records of the selection window with a home win probability."""
    out = []
    for r in records:
        game = r.prediction.game
        if game.season != season:  # cannot happen: the run predicts this season only
            raise RuntimeError(f"{game.game_id} of {game.season} outside the selection season")
        if r.prediction.p_home is None or r.log_loss is None:
            continue
        in_window = r.prediction.reported if select_from is None else game.et_date >= select_from
        if in_window:
            out.append(r)
    return out


def run_grid(
    conn: Connection,
    configurations: Sequence[Mapping[str, float]],
    config: WalkForwardConfig,
    *,
    select_from: date | None,
    now: datetime,
) -> tuple[list[Cell], CachedReads]:
    """Walk-forward + scoring of every configuration on one game load and one read memo."""
    if len(config.seasons) != 1:
        raise ValueError("a grid selects on exactly one season")
    season = config.seasons[0]
    games = load_games(conn)
    reads = CachedReads(conn)
    cells = []
    for index, params in enumerate(configurations):
        predictor = resolve_experiment(f"{ELO_EXPERIMENT}[{format_params(params)}]").make()
        run = run_walk_forward(reads, predictor, config, now=now, games=games)  # type: ignore[arg-type]
        chosen = selected(score_run(reads, run).records, season, select_from)  # type: ignore[arg-type]
        n = len(chosen)
        cells.append(
            Cell(
                index=index,
                params=dict(params),
                n=n,
                log_loss=sum(r.log_loss for r in chosen) / n if n else None,  # type: ignore[misc]
                brier=sum(r.brier for r in chosen) / n if n else None,  # type: ignore[misc]
                accuracy=sum(bool(r.correct) for r in chosen) / n if n else None,
            )
        )
    return cells, reads


def best_cell(cells: Sequence[Cell]) -> Cell:
    """Lowest log loss; ties go to the cell nearest the start, then to grid order."""
    scored = [c for c in cells if c.log_loss is not None]
    if not scored:
        raise ValueError("no cell has a scored game")
    lowest = min(c.log_loss for c in scored)  # type: ignore[type-var]
    tied = [c for c in scored if c.log_loss - lowest <= TIE_TOLERANCE]  # type: ignore[operator]
    return min(tied, key=lambda c: (c.distance, c.index))


def next_season(season: str) -> str:
    """'2023-24' -> '2024-25'."""
    first = int(season[:4]) + 1
    return f"{first}-{(first + 1) % 100:02d}"


def _value_list(values: Sequence[float]) -> str:
    """'10, 15, 20'."""
    return ", ".join(f"{v:g}" for v in values)


def render_grid(
    cells: Sequence[Cell],
    *,
    season: str,
    select_from: date | None,
    grid: Sequence[tuple[str, Sequence[float]]],
    fixed: Mapping[str, float],
    command: str,
    code_version: str | None,
    report_from: date,
    reads: CachedReads | None = None,
) -> str:
    """The markdown report of one grid search (selection season metrics only)."""
    start = start_cell(grid, fixed)
    start_row = next(c for c in cells if dict(c.params) == start)
    window = (
        f"reported games (ET date >= {report_from}, the baseline warm-up rule)"
        if select_from is None
        else f"games with ET date >= {select_from}"
    )
    searched = "; ".join(f"{key} = {_value_list(values)}" for key, values in grid)
    held = ", ".join(f"{key} = {fixed[key]:g}" for key in ELO_PARAMS if key in fixed)
    lines = [
        f"# Backtest grid: {ELO_EXPERIMENT} on {season} (selection season only)",
        "",
        f"- Command: `{command}`",
        f"- Code: commit {code_version or 'unknown'}. One REPEATABLE READ, READ ONLY "
        "transaction; nothing was written to the database. metric_version "
        f"{METRIC_VERSION}.",
        f"- Selection window: {season}, {window}; final games scored with a home win "
        "probability. Only this season is predicted and scored (design 6-4: choose on the "
        "selection season, report on the next one with one pre-registered comparison).",
        f"- Grid: {searched}"
        + (f"; fixed: {held}" if held else "")
        + f" -> {len(cells)} cells. Start cell: {format_params(start)} (D-049 M-7 starting "
        "values of the searched constants).",
        "- Best cell: lowest log loss; ties (|difference| <= "
        f"{TIE_TOLERANCE:g}) go to the cell nearest the start (|k - 20| / 5 + |hca - 100| / 25 "
        "+ |carry - 0.75| / 0.15), then to the first cell in grid order. No bootstrap.",
    ]
    if reads is not None:
        lines.append(
            f"- Reads: games loaded once; other reads executed {reads.executed} times and "
            f"reused {reads.reused} times across the cells."
        )
    lines.append("")
    if all(c.log_loss is None for c in cells):
        lines.append("No scored game in the selection window.")
        return "\n".join(lines) + "\n"
    best = best_cell(cells)
    ordered = sorted(cells, key=lambda c: (c.log_loss is None, c.log_loss or 0.0, c.index))
    rows = []
    for rank, c in enumerate(ordered, 1):
        notes = []
        if c is best:
            notes.append("best")
        if c is start_row:
            notes.append("start")
        delta = (
            c.log_loss - start_row.log_loss
            if c.log_loss is not None and start_row.log_loss is not None
            else None
        )
        rows.append(
            [
                str(rank),
                f"{c.params['k']:g}",
                f"{c.params['hca']:g}",
                f"{c.params['carry']:g}",
                str(c.n),
                _fmt(c.log_loss),
                _fmt(c.brier),
                _fmt(c.accuracy, 3),
                _fmt(delta, signed=True),
                f"{c.distance:g}",
                ", ".join(notes),
            ]
        )
    lines += ["## Cells (log loss ascending)", ""]
    lines += _table(
        (
            "rank",
            "k",
            "hca",
            "carry",
            "n",
            "log loss",
            "Brier",
            "accuracy",
            "dLL vs start",
            "distance to start",
            "note",
        ),
        rows,
    )
    assert best.log_loss is not None and best.brier is not None
    report = next_season(season)
    lines += [
        "",
        "## Best cell",
        "",
        f"- Best: {format_params(best.params)}: log loss {_fmt(best.log_loss)}, Brier "
        f"{_fmt(best.brier)}, accuracy {_fmt(best.accuracy, 3)}, n {best.n}.",
        f"- Start: {format_params(start_row.params)}: log loss {_fmt(start_row.log_loss)}, "
        f"Brier {_fmt(start_row.brier)}, accuracy {_fmt(start_row.accuracy, 3)}; best - start: "
        f"dLL {_fmt(_minus(best.log_loss, start_row.log_loss), signed=True)}, dBrier "
        f"{_fmt(_minus(best.brier, start_row.brier), signed=True)}"
        + (" (the best cell is the start cell)." if best is start_row else "."),
    ]
    if report in HOLDOUT_SEASONS:
        lines.append(
            f"- Next: the season after {season} is the holdout season {report}; the report "
            "season comes from the pre-registration, not from this command."
        )
    else:
        lines.append(
            "- Next (the one pre-registered comparison on the report season): "
            f'`uv run presagio backtest compare --experiments "{ELO_EXPERIMENT},'
            f'{best.experiment_id}" --reference {ELO_EXPERIMENT} --seasons {report}`'
        )
    return "\n".join(lines) + "\n"


def _minus(a: float | None, b: float | None) -> float | None:
    """a - b, None when either is None."""
    return None if a is None or b is None else a - b
