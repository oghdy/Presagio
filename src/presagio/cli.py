"""`presagio` command line: ingestion commands (backfill now; ingest-daily in P-017).

Exit codes: 0 everything done, 1 some unit not done (or an error before starting), 2 stopped by
a block (HTTP 403/429), 64 bad arguments (sysexits EX_USAGE, so it never reads as "blocked").
"""

from __future__ import annotations

import argparse
import logging
import logging.config
import sys
from collections.abc import Callable, Sequence
from datetime import date, datetime
from typing import Any, NoReturn

from sqlalchemy import Engine

from presagio.config import get_settings
from presagio.ingestion.backfill import (
    EXIT_FAILED,
    RUN,
    SEASON_TYPES,
    BackfillReport,
    BackfillRequest,
    BackfillSources,
    PlannedUnit,
    estimate,
    plan_backfill,
    run_backfill,
)
from presagio.ingestion.client import IngestionClient, db_raw_store
from presagio.ingestion.errors import RunAlreadyRunningError
from presagio.ingestion.runs import error_line

logger = logging.getLogger("presagio.cli")

EXIT_USAGE = 64
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
# Loggers that must stay at WARNING whatever -v says: httpx logs every request URL with its
# query string at INFO, and The Odds API key travels in the query string (D-031).
QUIET_LOGGERS = ("httpx", "httpcore")


class ArgumentParser(argparse.ArgumentParser):
    """argparse with exit code 64 for usage errors instead of 2 (2 means blocked here)."""

    def error(self, message: str) -> NoReturn:
        """Print usage and the message to stderr, then exit with EXIT_USAGE."""
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {message}\n")


def configure_logging(verbose: bool = False) -> None:
    """Log to stderr: our loggers at INFO (DEBUG with -v), everything else at WARNING."""
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"plain": {"format": LOG_FORMAT}},
            "handlers": {
                "stderr": {
                    "class": "logging.StreamHandler",
                    "formatter": "plain",
                    "stream": "ext://sys.stderr",
                }
            },
            "root": {"level": "WARNING", "handlers": ["stderr"]},
            "loggers": {
                "presagio": {"level": "DEBUG" if verbose else "INFO"},
                **{name: {"level": "WARNING"} for name in QUIET_LOGGERS},
            },
        }
    )


def iso_date(value: str) -> date:
    """argparse type for YYYY-MM-DD."""
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a date (YYYY-MM-DD): {value!r}") from None


def build_parser() -> ArgumentParser:
    """The presagio argument parser with its subcommands."""
    parser = ArgumentParser(prog="presagio", description=__doc__.splitlines()[0])
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="debug logs for presagio (never for httpx)"
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    backfill = commands.add_parser(
        "backfill",
        help="load past seasons (schedule, results, team stats), resumable",
        description=(
            "Load whole seasons from nba_api: teams once, then per season one schedule call "
            "and one game-log call per season type. Units that already succeeded after their "
            "season was over are skipped; run again to resume."
        ),
    )
    target = backfill.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--season", action="append", metavar="YYYY-YY", help="season, e.g. 2023-24 (repeatable)"
    )
    target.add_argument(
        "--from",
        dest="date_from",
        type=iso_date,
        metavar="YYYY-MM-DD",
        help="first ET date; the seasons the range touches are loaded whole (needs --to)",
    )
    backfill.add_argument(
        "--to", dest="date_to", type=iso_date, metavar="YYYY-MM-DD", help="last ET date"
    )
    backfill.add_argument(
        "--season-type",
        action="append",
        choices=SEASON_TYPES,
        help="only these game logs (repeatable; default: all)",
    )
    backfill.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan, call count and expected time; write nothing",
    )
    backfill.add_argument("--force", action="store_true", help="run units that already succeeded")
    backfill.add_argument(
        "--stale-after-hours",
        type=float,
        metavar="H",
        help="close 'running' rows older than H hours as aborted first (default STALE_RUN_HOURS)",
    )
    return parser


def format_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    """Plain text table; numbers right-aligned."""
    cells = [[str(h) for h in headers]] + [["" if c is None else str(c) for c in r] for r in rows]
    widths = [max(len(row[i]) for row in cells) for i in range(len(headers))]
    numeric = [
        all(isinstance(r[i], int | float) for r in rows) and bool(rows) for i in range(len(headers))
    ]
    lines = []
    for row in cells:
        parts = [
            cell.rjust(width) if is_num else cell.ljust(width)
            for cell, width, is_num in zip(row, widths, numeric, strict=True)
        ]
        lines.append("  ".join(parts).rstrip())
    lines.insert(1, "  ".join("-" * w for w in widths))
    return "\n".join(lines)


def describe_request(request: BackfillRequest) -> str:
    """One line naming what is backfilled."""
    where = f"seasons {', '.join(request.seasons)}"
    if request.date_from is not None:
        where = f"ET dates {request.date_from} to {request.date_to} -> {where} (whole seasons)"
    return f"{where}; game logs: {', '.join(request.season_types)}"


def print_plan(plan: Sequence[PlannedUnit], min_interval: float, dry_run: bool) -> None:
    """Units, run/skip with reasons, expected calls and time."""
    est = estimate(plan, min_interval)
    rows = [
        (
            n,
            p.unit.job_type,
            p.unit.scope_key,
            p.unit.endpoint,
            p.action,
            p.reason,
            p.unit.calls if p.action == RUN else 0,
            round(seconds, 1),
        )
        for n, (p, seconds) in enumerate(zip(plan, est.per_unit, strict=True), 1)
    ]
    headers = ("#", "job_type", "scope_key", "endpoint", "action", "reason", "calls", "est_s")
    print(format_table(headers, rows))
    print(
        f"{'Would make' if dry_run else 'Plan'}: {est.calls} call(s), about {est.seconds:.0f} s "
        f"({min_interval:g} s interval + assumed response time per call; retries not counted)"
    )


def print_report(report: BackfillReport) -> None:
    """Per-unit outcome table, the failure list and the exit status."""
    rows = []
    for n, r in enumerate(report.results, 1):
        items = (
            f"{r.items_succeeded}/{r.items_failed}/{'' if r.items_total is None else r.items_total}"
            if r.run_id is not None
            else ""
        )
        rows.append(
            (
                n,
                r.unit.job_type,
                r.unit.scope_key,
                r.status,
                r.api_calls,
                items,
                r.rows_written,
                "" if r.run_id is None else r.run_id,
                round(r.seconds, 1),
            )
        )
    headers = (
        "#",
        "job_type",
        "scope_key",
        "status",
        "calls",
        "ok/failed/total",
        "rows",
        "run_id",
        "s",
    )
    print()
    print(format_table(headers, rows))
    not_reached = report.plan[len(report.results) :]
    print(
        f"\nParent run {report.parent_run_id}: {report.parent_status}; "
        f"{sum(r.api_calls for r in report.results)} call(s)"
    )
    if report.games_in_range is not None:
        games, final = report.games_in_range
        print(
            f"Games in {report.request.date_from}..{report.request.date_to} (ET): "
            f"{games}, final {final}"
        )
    if report.failures or not_reached:
        print(f"\nFailures ({len(report.failures)}):")
        for r in report.failures:
            print(f"  - {r.unit.job_type} {r.unit.scope_key}: {r.status}: {r.error or '-'}")
        if not_reached:
            print(f"Not reached after the block ({len(not_reached)}):")
            for p in not_reached:
                print(f"  - {p.unit.job_type} {p.unit.scope_key}")
    if report.blocked is not None:
        print("\nStopped: the source blocked us (403/429). Wait before running again (D-024).")
    print(f"Exit code {report.exit_code}")


def backfill_request(args: argparse.Namespace) -> BackfillRequest:
    """BackfillRequest from parsed arguments; ValueError for a bad season or range."""
    season_types = args.season_type or SEASON_TYPES
    if args.date_from is not None:
        return BackfillRequest.for_range(
            args.date_from, args.date_to, season_types, force=args.force
        )
    return BackfillRequest.for_seasons(args.season, season_types, force=args.force)


def cmd_backfill(
    args: argparse.Namespace,
    request: BackfillRequest,
    *,
    engine: Engine | None,
    sources: Callable[[Engine], BackfillSources] | None,
    now: datetime | None,
) -> int:
    """presagio backfill: plan, then (unless --dry-run) run and report."""
    settings = get_settings()
    if engine is None:
        from presagio.db.session import get_engine

        engine = get_engine()
    stale_after_hours = (
        args.stale_after_hours if args.stale_after_hours is not None else settings.stale_run_hours
    )
    print(f"Backfill {describe_request(request)}{' (dry run: nothing is written)' * args.dry_run}")
    plan = plan_backfill(engine, request, now=now)
    print_plan(plan, settings.http_min_interval, args.dry_run)
    sys.stdout.flush()  # show the plan before the progress lines on stderr
    if args.dry_run:
        return 0
    if sources is None:
        built = BackfillSources.nba_api(IngestionClient(db_raw_store(engine)))
    else:
        built = sources(engine)
    try:
        report = run_backfill(engine, request, plan, built, stale_after_hours=stale_after_hours)
    except RunAlreadyRunningError as exc:
        print(
            f"\n{exc}. Another backfill with the same arguments is running, or one died less "
            f"than {stale_after_hours:g} h ago (see --stale-after-hours).",
            file=sys.stderr,
        )
        return EXIT_FAILED
    print_report(report)
    return report.exit_code


def main(
    argv: Sequence[str] | None = None,
    *,
    engine: Engine | None = None,
    sources: Callable[[Engine], BackfillSources] | None = None,
    now: datetime | None = None,
    setup_logging: bool = True,
) -> int:
    """Entry point (`presagio`); engine, sources and now are injectable for tests."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "backfill" and (args.date_from is None) != (args.date_to is None):
        parser.error("--from and --to go together")
    try:
        request = backfill_request(args)
    except ValueError as exc:  # bad season or range values
        parser.error(str(exc))
    if setup_logging:
        configure_logging(args.verbose)
    try:
        return cmd_backfill(args, request, engine=engine, sources=sources, now=now)
    except Exception as exc:  # e.g. database unreachable: one line, no traceback
        logger.debug("backfill failed", exc_info=True)
        print(f"presagio: {error_line(exc)}", file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
