"""The `presagio backtest run` subcommand (D-043: one command runs an experiment).

presagio.cli registers it; this module never imports presagio.cli (no import cycle). The
command reads the database in a READ ONLY transaction and prints the markdown report on
stdout. Storage of predictions and evaluations arrives with P-033; until then --dry-run and a
normal run differ only in the report's last header line.

Exit codes: 0 report printed, 1 failure (e.g. database unreachable), 64 bad arguments or a
locked holdout season (design 6-3, T10).
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Engine, text

from presagio.evaluation.clv import CLOSING_TOLERANCE
from presagio.evaluation.dummy import ConstantPredictor
from presagio.evaluation.holdout import HoldoutLockedError, check_holdout
from presagio.evaluation.report import ReportSettings, render_report
from presagio.evaluation.scoring import score_run
from presagio.evaluation.timing import DEFAULT_AVAILABILITY_LAG
from presagio.evaluation.walkforward import (
    DEFAULT_REFIT_EVERY_DAYS,
    Predictor,
    WalkForwardConfig,
    run_walk_forward,
)
from presagio.ingestion.runs import error_line

logger = logging.getLogger("presagio.evaluation")

COMMAND = "backtest"
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 64
SEASON_RE = re.compile(r"^(\d{4})-(\d{2})$")
# Experiment id -> predictor factory. P-033 adds the baselines (H-01 ...).
EXPERIMENTS: dict[str, Callable[[], Predictor]] = {"H-00": ConstantPredictor}


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


def add_backtest_parser(commands: Any) -> None:
    """Register `presagio backtest run` on the CLI's subparsers (called by presagio.cli)."""
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
        "--experiment", required=True, choices=sorted(EXPERIMENTS), help="experiment id"
    )
    run.add_argument(
        "--seasons", required=True, type=seasons_arg, metavar="S1,S2", help="e.g. 2023-24,2024-25"
    )
    run.add_argument(
        "--unlock-holdout",
        metavar="REASON",
        help='open the holdout season once: "<hypothesis> <reason>", e.g. "H-08 final model"',
    )
    run.add_argument(
        "--dry-run", action="store_true", help="compute and print; never write (always true now)"
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


def cmd_backtest_run(
    args: argparse.Namespace,
    *,
    engine: Engine | None = None,
    sources: Any = None,
    now: datetime | None = None,
) -> int:
    """presagio backtest run: holdout guard, walk-forward, scoring, markdown report."""
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
    predictor = EXPERIMENTS[args.experiment]()
    try:
        if engine is None:
            from presagio.db.session import get_engine

            engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            run = run_walk_forward(conn, predictor, config, now=now or datetime.now(UTC))
            scoring = score_run(conn, run)
            conn.rollback()
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
    )
    sys.stdout.write(report)
    return EXIT_OK
