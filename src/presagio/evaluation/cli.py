"""The `presagio backtest` subcommands (D-043: one command runs an experiment).

presagio.cli registers them; this module never imports presagio.cli (no import cycle).

- `run`: the walk-forward and the scoring of one experiment in a READ ONLY transaction, then,
  with --store, model_versions, predictions and evaluations in a second transaction
  (presagio.evaluation.store: append only, differences reported and never overwritten), and
  the markdown report on stdout. Without --store (or with --dry-run) nothing is written.
- `compare` (P-037 A1): several experiments on their common game set with paired bootstrap
  deltas against a reference experiment (presagio.evaluation.compare). Never writes.
- `grid` (P-037 A3): the Elo constants grid on one selection season
  (presagio.evaluation.grid). Never writes, never prints another season's metrics.

Experiment ids may carry B2 parameters, `B2[k=25,hca=75,carry=0.9]` (P-037 A2).

Exit codes: 0 report printed, 1 failure (e.g. database unreachable, configuration conflict),
3 a leakage guard fired (LeakageGuardError: a bug in a predictor or training source, logged
at ERROR), 4 (`run --store` only) stored and the report was printed but some stored rows
differ from this run (determinism check, design 8-3), 64 bad arguments or a locked holdout
season (6-3, T10).
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import Engine, text

from presagio.evaluation.clv import CLOSING_TOLERANCE
from presagio.evaluation.compare import begin_snapshot, command_line, render_compare, run_arms
from presagio.evaluation.experiments import (
    ELO_EXPERIMENT,
    ELO_START,
    EXPERIMENTS,
    ExperimentIdError,
    check_param_key,
    model_spec,
    parse_param_value,
    resolve_experiment,
    split_experiment_list,
)
from presagio.evaluation.grid import grid_cells, render_grid, run_grid, start_cell
from presagio.evaluation.holdout import HOLDOUT_SEASONS, HoldoutLockedError, check_holdout
from presagio.evaluation.report import ReportSettings, render_report
from presagio.evaluation.scoring import score_run
from presagio.evaluation.store import StoreError, store_run
from presagio.evaluation.timing import DEFAULT_AVAILABILITY_LAG
from presagio.evaluation.walkforward import (
    DEFAULT_REFIT_EVERY_DAYS,
    LeakageGuardError,
    WalkForwardConfig,
    run_walk_forward,
)
from presagio.ingestion.runs import current_code_version, error_line
from presagio.models.elo import EloBaseline

logger = logging.getLogger("presagio.evaluation")

COMMAND = "backtest"
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_LEAKAGE = 3
EXIT_MISMATCH = 4
EXIT_USAGE = 64
SEASON_RE = re.compile(r"^(\d{4})-(\d{2})$")


def seasons_arg(value: str) -> tuple[str, ...]:
    """argparse type for '2023-24,2024-25': seasons like 2023-24, no duplicates."""
    seasons = tuple(part.strip() for part in value.split(",") if part.strip())
    if not seasons:
        raise argparse.ArgumentTypeError("no season given")
    for season in seasons:
        match = SEASON_RE.match(season)
        if not match or (int(match.group(1)) + 1) % 100 != int(match.group(2)):
            raise argparse.ArgumentTypeError(f"not a season like 2023-24: {season!r}")
    if len(set(seasons)) != len(seasons):
        raise argparse.ArgumentTypeError(f"duplicate season in {value!r}")
    return seasons


def hours_arg(value: str) -> timedelta:
    """argparse type for a number of hours >= 0."""
    try:
        hours = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number of hours: {value!r}") from None
    if not hours >= 0:  # also rejects nan
        raise argparse.ArgumentTypeError(f"must be >= 0 hours: {value!r}")
    return timedelta(hours=hours)


def positive_int_arg(value: str) -> int:
    """argparse type for an integer >= 1."""
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {value!r}") from None
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1: {value!r}")
    return number


def experiment_arg(value: str) -> str:
    """argparse type for an experiment id (B2 also as B2[k=..,hca=..,carry=..]): the
    canonical id."""
    try:
        return resolve_experiment(value).id
    except ExperimentIdError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def experiment_list_arg(value: str) -> tuple[str, ...]:
    """argparse type for 'B0,B1,B2[k=25]': canonical ids, no duplicates."""
    try:
        ids = tuple(resolve_experiment(part).id for part in split_experiment_list(value))
    except ExperimentIdError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None
    if len(set(ids)) != len(ids):
        raise argparse.ArgumentTypeError(f"duplicate experiment in {value!r}")
    return ids


def season_arg(value: str) -> str:
    """argparse type for exactly one season like 2023-24."""
    seasons = seasons_arg(value)
    if len(seasons) != 1:
        raise argparse.ArgumentTypeError(f"exactly one season, got {value!r}")
    return seasons[0]


def date_arg(value: str) -> date:
    """argparse type for an ISO date YYYY-MM-DD."""
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a date like 2024-01-01: {value!r}") from None


def _elo_value(key: str, raw: str) -> float:
    """One B2 constant value, checked by EloBaseline; ExperimentIdError otherwise."""
    check_param_key(ELO_EXPERIMENT, key)
    value = parse_param_value(key, raw)
    try:
        EloBaseline(**{key: value})
    except ValueError as exc:
        raise ExperimentIdError(str(exc)) from None
    return value


def grid_param_arg(value: str) -> tuple[str, tuple[float, ...]]:
    """argparse type for 'k=10,15,20': a B2 constant and its distinct, valid values."""
    key, sep, raw = (part.strip() for part in value.partition("="))
    try:
        if not sep or not raw:
            raise ExperimentIdError(f"not key=v1,v2,...: {value!r}")
        values = tuple(_elo_value(key, v.strip()) for v in raw.split(","))
    except ExperimentIdError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None
    if len(set(values)) != len(values):
        raise argparse.ArgumentTypeError(f"duplicate value in {value!r}")
    return key, values


def fixed_arg(value: str) -> tuple[tuple[str, float], ...]:
    """argparse type for 'k=25,hca=75': B2 constants held at one valid value each."""
    pairs = []
    try:
        for item in value.split(","):
            key, sep, raw = (part.strip() for part in item.partition("="))
            if not sep or not raw:
                raise ExperimentIdError(f"not key=value: {item.strip()!r}")
            pairs.append((key, _elo_value(key, raw)))
    except ExperimentIdError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None
    return tuple(pairs)


def _experiment_help() -> str:
    """The registered experiments, one clause each."""
    return "; ".join(f"{e.id} = {e.description}" for e in EXPERIMENTS.values())


def add_backtest_parser(commands: Any) -> None:
    """Register `presagio backtest run|compare|grid` on the CLI's subparsers (presagio.cli)."""
    backtest = commands.add_parser(
        COMMAND,
        help="walk-forward backtest of an experiment (read only; prints a markdown report)",
        description="Walk-forward evaluation (modeling design v1.2 ch. 6).",
    )
    actions = backtest.add_subparsers(dest="backtest_action", required=True, metavar="ACTION")
    run = actions.add_parser(
        "run",
        help="run one experiment over seasons and print the metric tables",
        description=(
            "Predict every regular, play-in and playoff game of the seasons day by day "
            "(as_of = each US Eastern date 07:00 UTC), score the finished games and print "
            "markdown tables (all games / games with a market). The holdout season 2025-26 "
            "is refused without --unlock-holdout. Reads the database only."
        ),
    )
    run.add_argument(
        "--experiment",
        required=True,
        type=experiment_arg,
        metavar="ID",
        help="experiment id: "
        + _experiment_help()
        + '; B2 also takes constants, e.g. "B2[k=25,hca=75,carry=0.9]"',
    )
    run.add_argument(
        "--seasons", required=True, type=seasons_arg, metavar="S1,S2", help="e.g. 2023-24,2024-25"
    )
    run.add_argument(
        "--unlock-holdout",
        metavar="REASON",
        help='open the holdout season once: "<hypothesis> <reason>", e.g. "H-08 final model"',
    )
    writing = run.add_mutually_exclusive_group()
    writing.add_argument(
        "--store",
        action="store_true",
        help=(
            "write model_versions, predictions and evaluations (append only: existing rows "
            "are compared and never overwritten; locked holdout seasons are never written)"
        ),
    )
    writing.add_argument(
        "--dry-run", action="store_true", help="compute and print; write nothing (the default)"
    )
    run.add_argument(
        "--availability-lag-hours",
        dest="availability_lag",
        type=hours_arg,
        default=DEFAULT_AVAILABILITY_LAG,
        metavar="H",
        help="a result counts as known H hours after tip-off (default 3, rule R2)",
    )
    run.add_argument(
        "--refit-every-days",
        type=positive_int_arg,
        default=DEFAULT_REFIT_EVERY_DAYS,
        metavar="N",
        help="refit cadence of learning models (default 7)",
    )
    run.add_argument(
        "--closing-tolerance-hours",
        dest="closing_tolerance",
        type=hours_arg,
        default=CLOSING_TOLERANCE,
        metavar="H",
        help="closing odds count only with lead_time <= H hours (default 2)",
    )
    run.add_argument(
        "--bootstrap",
        type=positive_int_arg,
        default=ReportSettings.n_boot,
        metavar="N",
        help="bootstrap resamples (default 1000)",
    )
    run.add_argument("--seed", type=int, default=ReportSettings.seed, help="bootstrap seed")
    run.set_defaults(run=cmd_backtest_run)

    compare = actions.add_parser(
        "compare",
        help="several experiments on their common game set, deltas vs a reference",
        description=(
            "Run each experiment like `backtest run` (one REPEATABLE READ, READ ONLY "
            "transaction), keep the (game_id, as_of) pairs every experiment reported and "
            "scored with a home win probability (market_status available / pred_only when B3 "
            "takes part) and print per segment n, accuracy, log loss, Brier, ECE and the paired "
            "bootstrap deltas against the reference. Writes nothing; the holdout season is "
            "refused."
        ),
    )
    compare.add_argument(
        "--experiments",
        required=True,
        type=experiment_list_arg,
        metavar="ID1,ID2",
        help='at least two experiment ids, e.g. B0,B1,B2 or "B2,B2[k=25,hca=75,carry=0.9]"',
    )
    compare.add_argument(
        "--reference",
        required=True,
        type=experiment_arg,
        metavar="ID",
        help="the experiment the deltas are taken against (one of --experiments)",
    )
    compare.add_argument(
        "--seasons", required=True, type=seasons_arg, metavar="S1,S2", help="e.g. 2023-24,2024-25"
    )
    compare.add_argument(
        "--bootstrap",
        type=positive_int_arg,
        default=ReportSettings.n_boot,
        metavar="N",
        help="bootstrap resamples (default 1000)",
    )
    compare.add_argument("--seed", type=int, default=ReportSettings.seed, help="bootstrap seed")
    compare.set_defaults(run=cmd_backtest_compare)

    grid = actions.add_parser(
        "grid",
        help="Elo constant grid on one selection season (H-04(a), nested)",
        description=(
            "Run B2 for every combination of the --param values on one selection season and "
            "print n, log loss, Brier and accuracy per cell (log loss ascending), the best "
            "cell, its difference to the start cell and the compare command for the report "
            "season. Only the selection season is predicted and scored (design 6-4). Writes "
            "nothing; the holdout season is refused."
        ),
    )
    grid.add_argument(
        "--experiment",
        required=True,
        metavar="ID",
        help=f"the experiment whose constants are searched (only {ELO_EXPERIMENT})",
    )
    grid.add_argument(
        "--param",
        required=True,
        action="append",
        type=grid_param_arg,
        metavar="KEY=V1,V2",
        help="a searched constant and its values (k, hca or carry; repeatable; the grid "
        "must contain the starting value k=20 / hca=100 / carry=0.75)",
    )
    grid.add_argument(
        "--fixed",
        action="append",
        type=fixed_arg,
        default=[],
        metavar="KEY=V[,KEY=V]",
        help="constants held at one value (default: their starting values)",
    )
    grid.add_argument(
        "--select-season",
        required=True,
        type=season_arg,
        metavar="S",
        help="the one season the constants are chosen on, e.g. 2023-24",
    )
    grid.add_argument(
        "--select-from",
        type=date_arg,
        metavar="YYYY-MM-DD",
        help="score games from this ET date (default: the reported games, from 2024-01-01)",
    )
    grid.set_defaults(run=cmd_backtest_grid)


def cmd_backtest_run(
    args: argparse.Namespace,
    *,
    engine: Engine | None = None,
    sources: Any = None,
    now: datetime | None = None,
) -> int:
    """presagio backtest run: holdout guard, walk-forward, scoring, optional store, report."""
    del sources  # the backtest calls no external source
    try:
        holdout = check_holdout(args.seasons, args.unlock_holdout)
    except HoldoutLockedError as exc:
        print(f"presagio backtest: {exc}", file=sys.stderr)
        return EXIT_USAGE
    config = WalkForwardConfig(
        seasons=args.seasons,
        availability_lag=args.availability_lag,
        refit_every_days=args.refit_every_days,
        closing_tolerance=args.closing_tolerance,
        unlock_holdout=holdout.unlocked_reason,
    )
    predictor = resolve_experiment(args.experiment).make()
    if args.store and model_spec(predictor) is None:
        print(
            f"presagio backtest: experiment {args.experiment} is not a storable model "
            "(no ModelSpec); run it without --store",
            file=sys.stderr,
        )
        return EXIT_USAGE
    clock = now or datetime.now(UTC)
    try:
        if engine is None:
            from presagio.db.session import get_engine

            engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            run = run_walk_forward(conn, predictor, config, now=clock)
            scoring = score_run(conn, run)
            conn.rollback()
        store = None
        if args.store:
            with engine.begin() as conn:
                store = store_run(
                    conn,
                    run,
                    scoring,
                    predictor,
                    experiment_id=args.experiment,
                    seed=args.seed,
                    now=clock,
                )
    except LeakageGuardError as exc:  # a time-rule violation is a bug, never data to keep
        logger.error("leakage guard stopped the backtest: %s", exc)
        print(f"presagio backtest: leakage guard: {error_line(exc)}", file=sys.stderr)
        return EXIT_LEAKAGE
    except StoreError as exc:
        logger.error("store refused the run: %s", exc)
        print(f"presagio backtest: {error_line(exc)}", file=sys.stderr)
        return EXIT_FAILED
    except Exception as exc:  # e.g. database unreachable: one line, no traceback
        logger.debug("backtest failed", exc_info=True)
        print(f"presagio backtest: {error_line(exc)}", file=sys.stderr)
        return EXIT_FAILED
    report = render_report(
        run,
        scoring,
        experiment=args.experiment,
        settings=ReportSettings(n_boot=args.bootstrap, seed=args.seed),
        dry_run=args.dry_run,
        store=store,
    )
    sys.stdout.write(report)
    if store is not None and store.mismatches:
        return EXIT_MISMATCH
    return EXIT_OK


def _usage(message: str) -> int:
    """One stderr line, exit 64."""
    print(f"presagio backtest: {message}", file=sys.stderr)
    return EXIT_USAGE


def _locked(seasons: tuple[str, ...]) -> str | None:
    """The refusal message when a holdout season is requested (compare and grid never open
    it; `backtest run --unlock-holdout` is the only door, design 6-3)."""
    try:
        check_holdout(seasons, None)
    except HoldoutLockedError:
        locked = ", ".join(s for s in seasons if s in HOLDOUT_SEASONS)
        return (
            f"holdout season {locked} is locked (design 6-3, D-049 M-2); compare and grid "
            "never evaluate it"
        )
    return None


def _read_only_run(engine: Engine | None, work: Any) -> tuple[int, Any]:
    """Run `work(conn)` in one REPEATABLE READ, READ ONLY transaction: (exit code, result)."""
    try:
        if engine is None:
            from presagio.db.session import get_engine

            engine = get_engine()
        with engine.connect() as conn:
            begin_snapshot(conn)
            result = work(conn)
            conn.rollback()
    except LeakageGuardError as exc:  # a time-rule violation is a bug, never data to keep
        logger.error("leakage guard stopped the backtest: %s", exc)
        print(f"presagio backtest: leakage guard: {error_line(exc)}", file=sys.stderr)
        return EXIT_LEAKAGE, None
    except Exception as exc:  # e.g. database unreachable: one line, no traceback
        logger.debug("backtest failed", exc_info=True)
        print(f"presagio backtest: {error_line(exc)}", file=sys.stderr)
        return EXIT_FAILED, None
    return EXIT_OK, result


def cmd_backtest_compare(
    args: argparse.Namespace,
    *,
    engine: Engine | None = None,
    sources: Any = None,
    now: datetime | None = None,
) -> int:
    """presagio backtest compare: experiments on their common game set, paired deltas."""
    del sources  # the backtest calls no external source
    ids: tuple[str, ...] = args.experiments
    if len(ids) < 2:
        return _usage("compare needs at least two experiments")
    if args.reference not in ids:
        return _usage(f"reference {args.reference} is not one of --experiments {','.join(ids)}")
    refusal = _locked(args.seasons)
    if refusal:
        return _usage(refusal)
    config = WalkForwardConfig(seasons=args.seasons)
    experiments = [resolve_experiment(i) for i in ids]
    clock = now or datetime.now(UTC)
    code, arms = _read_only_run(engine, lambda conn: run_arms(conn, experiments, config, now=clock))
    if code != EXIT_OK:
        return code
    command = command_line(
        [
            "backtest",
            "compare",
            "--experiments",
            ",".join(ids),
            "--reference",
            args.reference,
            "--seasons",
            ",".join(args.seasons),
            "--bootstrap",
            str(args.bootstrap),
            "--seed",
            str(args.seed),
        ]
    )
    sys.stdout.write(
        render_compare(
            arms,
            reference=args.reference,
            settings=ReportSettings(n_boot=args.bootstrap, seed=args.seed),
            command=command,
            code_version=current_code_version(),
        )
    )
    return EXIT_OK


def cmd_backtest_grid(
    args: argparse.Namespace,
    *,
    engine: Engine | None = None,
    sources: Any = None,
    now: datetime | None = None,
) -> int:
    """presagio backtest grid: B2 constants searched on one selection season."""
    del sources  # the backtest calls no external source
    if args.experiment != ELO_EXPERIMENT:
        return _usage(
            f"grid searches the constants of {ELO_EXPERIMENT} only, not {args.experiment!r}"
        )
    grid: list[tuple[str, tuple[float, ...]]] = args.param
    fixed_pairs = [pair for group in args.fixed for pair in group]
    keys = [key for key, _ in grid] + [key for key, _ in fixed_pairs]
    repeated = sorted({key for key in keys if keys.count(key) > 1})
    if repeated:
        return _usage(f"constant given twice across --param/--fixed: {', '.join(repeated)}")
    for key, values in grid:
        if ELO_START[key] not in values:
            return _usage(
                f"the {key} grid must contain the starting value {ELO_START[key]:g} "
                "(D-049 M-7), so the difference to the start is measured in the same run"
            )
    season: str = args.select_season
    refusal = _locked((season,))
    if refusal:
        return _usage(refusal)
    fixed = dict(fixed_pairs)
    configurations = grid_cells(grid, fixed)
    config = WalkForwardConfig(seasons=(season,))
    clock = now or datetime.now(UTC)
    code, result = _read_only_run(
        engine,
        lambda conn: run_grid(
            conn, configurations, config, select_from=args.select_from, now=clock
        ),
    )
    if code != EXIT_OK:
        return code
    cells, reads = result
    argv = ["backtest", "grid", "--experiment", ELO_EXPERIMENT]
    for key, values in grid:
        argv += ["--param", f"{key}=" + ",".join(f"{v:g}" for v in values)]
    if fixed_pairs:
        argv += ["--fixed", ",".join(f"{k}={v:g}" for k, v in fixed_pairs)]
    argv += ["--select-season", season]
    if args.select_from is not None:
        argv += ["--select-from", args.select_from.isoformat()]
    assert start_cell(grid, fixed) in configurations
    sys.stdout.write(
        render_grid(
            cells,
            season=season,
            select_from=args.select_from,
            grid=grid,
            fixed=fixed,
            command=command_line(argv),
            code_version=current_code_version(),
            report_from=config.baseline_report_from,
            reads=reads,
        )
    )
    if all(c.n == 0 for c in cells):
        print("presagio backtest: no scored game in the selection window", file=sys.stderr)
        return EXIT_FAILED
    return EXIT_OK
