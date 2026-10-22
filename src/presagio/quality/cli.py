"""The `presagio quality-check` subcommand: parser setup and command body.

presagio.cli registers it; this module never imports presagio.cli (no import cycle).
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections.abc import Callable
from datetime import date

from sqlalchemy import Engine

from presagio.ingestion.runs import error_line
from presagio.quality.checks import CHECK_NAMES, CheckSettings, run_checks
from presagio.quality.report import EXIT_FAILED, render_json, render_text

logger = logging.getLogger("presagio.quality")

COMMAND = "quality-check"
SEASON_RE = re.compile(r"^(\d{4})-(\d{2})$")


def season_arg(value: str) -> str:
    """argparse type for a season like 2023-24 (second year = first + 1)."""
    match = SEASON_RE.match(value)
    if not match or (int(match.group(1)) + 1) % 100 != int(match.group(2)):
        raise argparse.ArgumentTypeError(f"not a season like 2023-24: {value!r}")
    return value


def date_arg(value: str) -> date:
    """argparse type for YYYY-MM-DD."""
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a date (YYYY-MM-DD): {value!r}") from None


def count_arg(minimum: int) -> Callable[[str], int]:
    """argparse type for an integer >= minimum."""

    def parse(value: str) -> int:
        """Parse and range-check one value."""
        try:
            number = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError(f"not an integer: {value!r}") from None
        if number < minimum:
            raise argparse.ArgumentTypeError(f"must be >= {minimum}: {value!r}")
        return number

    return parse


def add_quality_check_parser(commands: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Add the quality-check subcommand to presagio's subparsers."""
    defaults = CheckSettings()
    parser = commands.add_parser(
        COMMAND,
        help="check that the stored data makes sense (read only)",
        description=(
            "Run read-only data quality checks and print one row per check. Exit code 0 when "
            "nothing failed (warnings allowed), 1 when a check failed or could not run, "
            "64 for bad arguments."
        ),
    )
    parser.add_argument(
        "--season", type=season_arg, metavar="YYYY-YY", help="only this season (default: all)"
    )
    parser.add_argument(
        "--date",
        dest="date_et",
        type=date_arg,
        metavar="YYYY-MM-DD",
        help="only games on this US Eastern date",
    )
    parser.add_argument(
        "--check",
        action="append",
        choices=CHECK_NAMES,
        metavar="NAME",
        help=f"only these checks (repeatable): {', '.join(CHECK_NAMES)}",
    )
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument(
        "--games-tolerance",
        type=count_arg(0),
        default=defaults.regular_games_tolerance,
        metavar="N",
        help=(
            f"allowed distance from {defaults.expected_regular_games} regular games for a "
            f"finished season (default {defaults.regular_games_tolerance})"
        ),
    )
    parser.add_argument(
        "--recent-days",
        type=count_arg(1),
        default=defaults.recent_run_days,
        metavar="N",
        help=f"days of ingestion runs to look at (default {defaults.recent_run_days})",
    )
    return parser


def cmd_quality_check(args: argparse.Namespace, *, engine: Engine | None = None) -> int:
    """presagio quality-check: run the checks, print the report, return its exit code."""
    settings = CheckSettings(
        regular_games_tolerance=args.games_tolerance, recent_run_days=args.recent_days
    )
    try:
        if engine is None:
            from presagio.db.session import get_engine

            engine = get_engine()
        report = run_checks(
            engine, season=args.season, date_et=args.date_et, checks=args.check, settings=settings
        )
    except Exception as exc:  # e.g. database unreachable: one line, no traceback
        logger.debug("quality check could not start", exc_info=True)
        message = error_line(exc)
        if args.json:
            print(json.dumps({"ok": False, "exit_code": EXIT_FAILED, "error": message}))
        print(f"presagio: {message}", file=sys.stderr)
        return EXIT_FAILED
    print(render_json(report) if args.json else render_text(report))
    return report.exit_code
