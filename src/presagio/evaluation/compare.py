"""`presagio backtest compare` (P-037 A1): several experiments on one common game set.

Every experiment runs the same walk-forward and scoring as `backtest run` (one predictor per
experiment, same config, same READ ONLY transaction), then the tables are computed on the
**common game set** only: the (game_id, as_of) pairs that are reported (non-learning warm-up
before 2024-01-01 excluded), final and scored with a home win probability in *every*
experiment. When an experiment is a market baseline (B3), the set is further limited to
market_status available or pred_only (design 4-6 set (b): market experiments use (b) only).

Segments: season x (regular / postseason), the season total when a season has both phases,
and all seasons together when more than one season is compared (a total that would repeat
the row set just above it is not printed twice). Per segment and experiment: n, accuracy,
log loss [90%], Brier [90%], ECE, and against the reference experiment dLL and dBrier
(experiment - reference, negative = experiment better) with a paired bootstrap interval,
and dAccuracy. Every resample of a segment draws the same game indices for all experiments
and for both columns of a delta (metrics.bootstrap_indices with one seed and one n), so the
intervals are paired. One "always home" row per segment gives the accuracy of always picking
the home team (= the home win rate, D-055 item 6).

Reads are memoised for the length of the run (CachedReads): the games are loaded once and
each distinct (SQL, parameters) read of presagio.evaluation.data / clv runs once, so a
second experiment over the same days costs no database round trip. Nothing is written.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Hashable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, Row, RowMapping, text

from presagio.evaluation.data import load_games
from presagio.evaluation.experiments import Experiment, model_spec
from presagio.evaluation.market import B3_STATUSES
from presagio.evaluation.metrics import (
    Interval,
    bootstrap_interval,
    bootstrap_mean_interval,
    calibration_table,
    expected_calibration_error,
)
from presagio.evaluation.report import ReportSettings, _fmt, _fmt_ci, _table
from presagio.evaluation.scoring import METRIC_VERSION, EvaluationRecord, ScoringResult, score_run
from presagio.evaluation.walkforward import WalkForwardConfig, WalkForwardResult, run_walk_forward

# One snapshot for every experiment of a run: the same games, quotes and closing lines.
READ_ONLY_SNAPSHOT = "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
ALL = "all"
REGULAR = "regular"
POSTSEASON = "postseason"
ALWAYS_HOME = "always home (= home win rate)"
_SAFE_ARG = re.compile(r"^[A-Za-z0-9_.,=:/-]+$")

Key = tuple[str, datetime]  # (game_id, as_of)


def _freeze(value: Any) -> Hashable:
    """A hashable copy of bind parameters (dicts and lists become sorted tuples / tuples)."""
    if isinstance(value, dict):
        return tuple(sorted((k, _freeze(v)) for k, v in value.items()))
    if isinstance(value, list | tuple):
        return tuple(_freeze(v) for v in value)
    return value


class _CachedResult:
    """The rows of one memoised statement, offering what the evaluation readers use."""

    def __init__(self, rows: Sequence[Row[Any]]) -> None:
        """Hold the fetched rows."""
        self._rows = rows

    def mappings(self) -> list[RowMapping]:
        """Rows as mappings (column name -> value)."""
        return [row._mapping for row in self._rows]

    def scalars(self) -> list[Any]:
        """First column of every row."""
        return [row[0] for row in self._rows]

    def __iter__(self) -> Iterator[Row[Any]]:
        """The rows."""
        return iter(self._rows)


class CachedReads:
    """Read-through memo over one READ ONLY connection: each distinct (SQL text, bind
    parameters) is executed once and later identical reads get the same rows.

    Only for reads whose result depends on nothing but their text and parameters inside one
    snapshot (REPEATABLE READ): the evaluation readers (prediction-time quotes with as_of as a
    bind parameter, event matching, closing lines). It changes no rule: a predictor still sees
    only the PredictionContext the runner builds from these rows.
    """

    def __init__(self, conn: Connection) -> None:
        """Wrap the connection; nothing is read yet."""
        self._conn = conn
        self._rows: dict[tuple[str, Hashable], list[Row[Any]]] = {}
        self.executed = 0
        self.reused = 0

    def execute(self, statement: Any, parameters: dict[str, Any] | None = None) -> _CachedResult:
        """The rows of the statement, from the database the first time only."""
        params = parameters or {}
        key = (str(statement), _freeze(params))
        rows = self._rows.get(key)
        if rows is None:
            rows = list(self._conn.execute(statement, params))
            self._rows[key] = rows
            self.executed += 1
        else:
            self.reused += 1
        return _CachedResult(rows)


def begin_snapshot(conn: Connection) -> None:
    """Make the connection's transaction one REPEATABLE READ, READ ONLY snapshot."""
    conn.execute(text(READ_ONLY_SNAPSHOT))


@dataclass(frozen=True)
class Arm:
    """One experiment's run and scoring."""

    experiment: Experiment
    predictor_name: str
    version: str | None
    run: WalkForwardResult
    scoring: ScoringResult


def run_arms(
    conn: Connection,
    experiments: Sequence[Experiment],
    config: WalkForwardConfig,
    *,
    now: datetime,
) -> list[Arm]:
    """Walk-forward + scoring of every experiment over one game load and one read memo."""
    games = load_games(conn)
    reads = CachedReads(conn)
    arms = []
    for experiment in experiments:
        predictor = experiment.make()
        run = run_walk_forward(reads, predictor, config, now=now, games=games)  # type: ignore[arg-type]
        scoring = score_run(reads, run)  # type: ignore[arg-type]
        spec = model_spec(predictor)
        arms.append(Arm(experiment, predictor.name, spec.version if spec else None, run, scoring))
    return arms


def eligible(records: Sequence[EvaluationRecord]) -> dict[Key, EvaluationRecord]:
    """Reported, scored records with a home win probability, by (game_id, as_of)."""
    return {
        (r.prediction.game.game_id, r.prediction.as_of): r
        for r in records
        if r.prediction.reported and r.prediction.p_home is not None and r.log_loss is not None
    }


@dataclass(frozen=True)
class CommonSet:
    """The common game set and why each experiment's other records are outside it."""

    keys: tuple[Key, ...]  # in start order
    market_only: bool
    by_arm: tuple[dict[Key, EvaluationRecord], ...]
    outside: tuple[Counter[str], ...]  # per arm: reason -> count


def common_set(arms: Sequence[Arm]) -> CommonSet:
    """Intersection of the experiments' eligible records (market_status B3 set when a market
    experiment takes part); each arm's left-out records are counted by reason."""
    by_arm = tuple(eligible(arm.scoring.records) for arm in arms)
    market_only = any(arm.experiment.uses_market for arm in arms)
    keys = set(by_arm[0])
    for records in by_arm[1:]:
        keys &= set(records)
    if market_only:
        keys = {k for k in keys if by_arm[0][k].market_status in B3_STATUSES}
    outside: list[Counter[str]] = []
    for i, records in enumerate(by_arm):
        reasons: Counter[str] = Counter()
        for key, record in records.items():
            if key in keys:
                continue
            missing = [
                arms[j].experiment.id
                for j, other in enumerate(by_arm)
                if j != i and key not in other
            ]
            for name in missing:
                reasons[f"not in {name}"] += 1
            if not missing and market_only:
                reasons[f"market_status {record.market_status}"] += 1
        outside.append(reasons)

    def order(key: Key) -> tuple[datetime, str, datetime]:
        """Start order of the game, then game id and as_of."""
        game = by_arm[0][key].prediction.game
        return (game.start_time_utc, game.game_id, key[1])

    return CommonSet(tuple(sorted(keys, key=order)), market_only, by_arm, tuple(outside))


def segments(common: CommonSet, seasons: Sequence[str]) -> list[tuple[str, str, tuple[Key, ...]]]:
    """(season, phase, keys): season x phase, the season total when it has both phases, and
    all seasons when more than one season has games."""
    first = common.by_arm[0]
    out: list[tuple[str, str, tuple[Key, ...]]] = []
    seasons_with_games = 0
    for season in seasons:
        in_season = [k for k in common.keys if first[k].prediction.game.season == season]
        if not in_season:
            continue
        seasons_with_games += 1
        phases = []
        for phase in (REGULAR, POSTSEASON):
            chosen = tuple(k for k in in_season if first[k].is_postseason == (phase == POSTSEASON))
            if chosen:
                phases.append((season, phase, chosen))
        out += phases
        if len(phases) > 1:
            out.append((season, ALL, tuple(in_season)))
    if seasons_with_games > 1:
        out.append((ALL, ALL, common.keys))
    return out


def _accuracy(records: Sequence[EvaluationRecord]) -> float:
    """Share of correct picks (p >= 0.5 picks home)."""
    return sum(bool(r.correct) for r in records) / len(records)


def segment_rows(
    season: str,
    phase: str,
    keys: Sequence[Key],
    arms: Sequence[Arm],
    common: CommonSet,
    reference: int,
    s: ReportSettings,
) -> list[list[str]]:
    """One row per experiment and the always-home row of one segment."""
    n = len(keys)
    note = f"reference only (n < {s.small_sample})" if n < s.small_sample else ""
    columns = []
    for records in common.by_arm:
        chosen = [records[k] for k in keys]
        columns.append(
            (
                chosen,
                [r.log_loss for r in chosen],
                [r.brier for r in chosen],
            )
        )
    ref_records, ref_ll, ref_br = columns[reference]
    ref_acc = _accuracy(ref_records)
    rows = []
    for arm, (chosen, ll, br) in zip(arms, columns, strict=True):
        ps = [r.prediction.p_home for r in chosen]
        ys = [int(r.home_won) for r in chosen]
        accuracy = _accuracy(chosen)
        rows.append(
            [
                season,
                phase,
                arm.experiment.id,
                str(n),
                _fmt(accuracy, 3),
                _fmt_ci(_mean_ci(ll, s)),
                _fmt_ci(_mean_ci(br, s)),
                _fmt(expected_calibration_error(calibration_table(ps, ys))),
                _fmt_ci(_paired_delta(ll, ref_ll, s), signed=True),
                _fmt_ci(_paired_delta(br, ref_br, s), signed=True),
                _fmt(accuracy - ref_acc, 3, signed=True),
                note,
            ]
        )
    home_rate = sum(r.home_won for r in ref_records) / n
    rows.append([season, phase, ALWAYS_HOME, str(n), _fmt(home_rate, 3), *["-"] * 6, note])
    return rows


def _mean_ci(values: Sequence[float], s: ReportSettings) -> Interval:
    """Bootstrap interval of a mean (indices from the segment's n and the seed)."""
    return bootstrap_mean_interval(values, n_boot=s.n_boot, seed=s.seed, level=s.level)


def _difference(model: object, reference: object) -> object:
    """model - reference (floats or numpy arrays)."""
    return model - reference  # type: ignore[operator]


def _paired_delta(a: Sequence[float], b: Sequence[float], s: ReportSettings) -> Interval:
    """Paired bootstrap interval of mean(a) - mean(b): both columns use the same indices."""
    return bootstrap_interval([a, b], _difference, n_boot=s.n_boot, seed=s.seed, level=s.level)


def command_line(argv: Sequence[str]) -> str:
    """`presagio ...` with arguments double-quoted where a shell would need it."""
    parts = ["presagio"]
    for arg in argv:
        parts.append(arg if _SAFE_ARG.match(arg) else '"' + arg.replace('"', '\\"') + '"')
    return " ".join(parts)


def _counts(counter: Counter[str]) -> str:
    """'a 1, b 2' sorted by key, 'none' when empty."""
    return ", ".join(f"{k} {v}" for k, v in sorted(counter.items())) or "none"


def arm_table(arms: Sequence[Arm], common: CommonSet) -> list[str]:
    """Per experiment: what it predicted and why records are outside the common set."""
    rows = []
    for arm, records, outside in zip(arms, common.by_arm, common.outside, strict=True):
        predictions = arm.run.predictions
        warmup = sum(1 for p in predictions if not p.reported)
        scored = [r for r in arm.scoring.records if r.prediction.reported]
        reported = len(predictions) - warmup
        no_p_home = sum(1 for r in scored if r.prediction.p_home is None or r.log_loss is None)
        not_predicted = _counts(Counter(dict(arm.run.skipped)))
        if arm.run.not_predicted:
            by_status = Counter(n.market_status for n in arm.run.not_predicted)
            not_predicted += f" (by market_status: {_counts(by_status)})"
        rows.append(
            [
                arm.experiment.id,
                arm.predictor_name,
                arm.version or "-",
                str(len(predictions)),
                not_predicted,
                str(warmup),
                str(reported - len(scored)),
                str(no_p_home),
                str(len(records)),
                str(len(records) - len(common.keys)),
                _counts(outside) if outside else "-",
            ]
        )
    return _table(
        (
            "experiment",
            "model",
            "version",
            "predictions",
            "not predicted",
            "warm-up",
            "not final",
            "no p_home",
            "eligible",
            "outside common set",
            "why outside (a game can count under several)",
        ),
        rows,
    )


def render_compare(
    arms: Sequence[Arm],
    *,
    reference: str,
    settings: ReportSettings,
    command: str,
    code_version: str | None,
) -> str:
    """The markdown report of one comparison."""
    s = settings
    cfg = arms[0].run.config
    holdout = arms[0].run.holdout
    common = common_set(arms)
    ref_index = next(i for i, a in enumerate(arms) if a.experiment.id == reference)
    ids = ", ".join(a.experiment.id for a in arms)
    lines = [
        f"# Backtest compare: {ids} (reference {reference})",
        "",
        f"- Command: `{command}`",
        f"- Code: commit {code_version or 'unknown'}. One REPEATABLE READ, READ ONLY "
        "transaction; nothing was written to the database.",
        f"- Seasons: {', '.join(cfg.seasons)}. Holdout "
        + (
            f"UNLOCKED for {', '.join(holdout.holdout_seasons)}: {holdout.unlocked_reason}"
            if holdout.unlocked
            else f"locked ({', '.join(cfg.holdout_seasons)} not evaluated)"
        )
        + ". as_of: each US Eastern date 07:00 UTC; every experiment runs the same walk-forward "
        f"as `backtest run` (baselines reported from {cfg.baseline_report_from}).",
        "- Common game set: (game_id, as_of) reported, final and scored with a home win "
        "probability in every experiment"
        + (
            "; market_status available or pred_only because a market experiment takes part "
            "(design 4-6 set (b))"
            if common.market_only
            else ""
        )
        + f": n = {len(common.keys)}.",
        f"- Uncertainty: game-level bootstrap, {s.n_boot} resamples, seed {s.seed}, "
        f"{s.level:.0%} percentile interval; within a segment every experiment and both "
        "columns of a delta use the same resampled games (paired). "
        f"d = experiment - reference ({reference}); negative dLL / dBrier = experiment better. "
        f"Segments with n < {s.small_sample} are reference only. metric_version "
        f"{METRIC_VERSION}.",
        "",
        "## Experiments and the common set",
        "",
        *arm_table(arms, common),
        "",
    ]
    if not common.keys:
        lines.append("No common scored predictions.")
        return "\n".join(lines) + "\n"
    lines += [
        "## Metrics on the common set (lower is better except accuracy)",
        "",
        "A total row is printed only when it differs from the rows above it (a season with "
        "one phase has no separate season total; one season has no all-seasons row).",
        "",
    ]
    rows: list[list[str]] = []
    for season, phase, keys in segments(common, cfg.seasons):
        rows += segment_rows(season, phase, keys, arms, common, ref_index, s)
    lines += _table(
        (
            "season",
            "phase",
            "experiment",
            "n",
            "accuracy",
            "log loss [90%]",
            "Brier [90%]",
            "ECE",
            f"dLL vs {reference} [90%]",
            f"dBrier vs {reference} [90%]",
            f"dAccuracy vs {reference}",
            "note",
        ),
        rows,
    )
    return "\n".join(lines) + "\n"
