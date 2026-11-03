"""The `presagio features compute` subcommand (P-035 criterion 5).

presagio.cli registers it and hands in the time policy (FeaturePolicy: the backtest as_of
of an ET date and the holdout guard, both from presagio.evaluation). This package never
imports presagio.evaluation (design 9-2 Q4 / 9-3 T6, enforced over every file of the
package by tests/evaluation/test_boundaries.py), so the policy is injected at the CLI
layer instead; this module never imports presagio.cli either (no import cycle).

    presagio features compute --seasons 2023-24,2024-25 [--version fv1] (--dry-run | --store)
        one row per predicted-type game (regular, play-in, playoffs) of the seasons, per ET
        date d at as_of = policy.backtest_as_of(d) (d 07:00 UTC, D-049 M-1), origin
        backtest, kind daily; a holdout season is refused without --unlock-holdout
        "<hypothesis and reason>" (design 6-3, the same gate as the backtest)
    presagio features compute --date YYYY-MM-DD --origin live (--dry-run | --store)
        as_of = now (UTC); the games of that ET date that have not started yet; origin live
    presagio features compute --date YYYY-MM-DD [--origin backtest] ...
        as_of = that date's backtest as_of (gap filling of a missed day, D-049 follow-up)

Output: `features fv1: computed N on D days; inserted a, already stored b, differing c`
(a dry run reports key count and NULL share instead of the store counts).
Exit codes: 0 done, 4 some stored rows differ from this run (never overwritten; the same
code as the backtest's determinism check, D-055), 64 argument error (the repository's usage
code: holdout locked, an impossible option combination, argparse errors), 1 failure
(database unreachable, store refused).
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import partial
from typing import Any

from sqlalchemy import Engine, text

from presagio.features.compute import FeatureRow, compute_day
from presagio.features.manifest import FEATURE_VERSIONS, PREDICTED_GAME_TYPES
from presagio.features.store import FeatureStoreError, FeatureStoreSummary, store_features
from presagio.ingestion.runs import error_line

logger = logging.getLogger("presagio.features")

COMMAND = "features"
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_ARGS = 64  # sysexits EX_USAGE, like every presagio subcommand (and backtest's holdout)
EXIT_MISMATCH = 4
ORIGIN_BACKTEST = "backtest"
ORIGIN_LIVE = "live"
SNAPSHOT_KIND = "daily"
SEASON_RE = re.compile(r"^(\d{4})-(\d{2})$")


@dataclass(frozen=True)
class FeaturePolicy:
    """The evaluation-layer rules the command needs, injected by presagio.cli."""

    backtest_as_of: Callable[[date], datetime]
    check_holdout: Callable[[Sequence[str], str | None], Any]
    holdout_error: type[Exception]


# ET dates with a predicted-type game of the seasons.
SEASON_DAYS_SQL = text(
    """
    SELECT DISTINCT (start_time_utc AT TIME ZONE 'America/New_York')::date AS day
    FROM games
    WHERE season = ANY(:seasons) AND game_type = ANY(:game_types)
    ORDER BY 1
    """
)
DAY_SEASONS_SQL = text(
    """
    SELECT DISTINCT season FROM games
    WHERE game_type = ANY(:game_types)
      AND (start_time_utc AT TIME ZONE 'America/New_York')::date = :day
    """
)


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


def date_arg(value: str) -> date:
    """argparse type for YYYY-MM-DD."""
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a date (YYYY-MM-DD): {value!r}") from None


def add_features_parser(commands: Any, policy: FeaturePolicy) -> None:
    """Register `presagio features compute` on the CLI's subparsers (called by presagio.cli)."""
    features = commands.add_parser(
        COMMAND,
        help="compute as_of features (fv1) and store them append-only",
        description="Feature rows of modeling design v1.2 ch. 3 (P-035).",
    )
    actions = features.add_subparsers(dest="features_action", required=True, metavar="ACTION")
    compute = actions.add_parser(
        "compute",
        help="compute the features of whole seasons (backtest) or one ET date (live)",
        description=(
            "Compute one features row per regular, play-in and playoff game. With --seasons, "
            "every game of the seasons at as_of = its ET date 07:00 UTC (origin backtest); "
            "with --date --origin live, the games of that ET date that have not started at "
            "now (as_of = now). Rows are inserted ON CONFLICT DO NOTHING and never "
            "overwritten: a stored row with other values is reported (exit code 4)."
        ),
    )
    target = compute.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--seasons", type=seasons_arg, metavar="S1,S2", help="e.g. 2023-24,2024-25 (backtest)"
    )
    target.add_argument("--date", type=date_arg, metavar="YYYY-MM-DD", help="one US Eastern date")
    compute.add_argument(
        "--origin",
        choices=(ORIGIN_BACKTEST, ORIGIN_LIVE),
        default=ORIGIN_BACKTEST,
        help="live: as_of = now, games not started yet (--date only); default backtest",
    )
    compute.add_argument(
        "--version",
        dest="feature_version",
        choices=sorted(FEATURE_VERSIONS),
        default="fv1",
        help="feature version (default fv1)",
    )
    compute.add_argument(
        "--unlock-holdout",
        metavar="REASON",
        help='open the holdout season once: "<hypothesis> <reason>", e.g. "H-08 final model"',
    )
    writing = compute.add_mutually_exclusive_group(required=True)
    writing.add_argument(
        "--store", action="store_true", help="write the rows (append only, never overwritten)"
    )
    writing.add_argument(
        "--dry-run", action="store_true", help="compute and summarise; write nothing"
    )
    compute.set_defaults(run=partial(cmd_features_compute, policy=policy))


def null_summary(rows: list[FeatureRow]) -> str:
    """Key count and NULL share of a batch, with the keys that are NULL most often."""
    if not rows:
        return "keys 0, null share n/a"
    keys = list(rows[0].values)
    nulls = Counter(k for row in rows for k in keys if row.values.get(k) is None)
    total = len(keys) * len(rows)
    share = sum(nulls.values()) / total
    worst = ", ".join(f"{k} {n / len(rows):.0%}" for k, n in nulls.most_common(5))
    return f"keys {len(keys)}, null share {share:.1%}" + (f" (most: {worst})" if worst else "")


def summary_line(
    version: str, computed: int, days: int, store: FeatureStoreSummary | None, dry: str | None
) -> str:
    """The one output line."""
    head = f"features {version}: computed {computed} on {days} days; "
    if store is None:
        return head + f"dry run (nothing written); {dry}"
    return head + (
        f"inserted {store.inserted}, already stored {store.already_stored}, "
        f"differing {store.differing}"
    )


def _days(
    args: argparse.Namespace, engine: Engine, now: datetime, policy: FeaturePolicy
) -> list[tuple[date, datetime]]:
    """(ET date, as_of) pairs to compute; the policy's error for a locked holdout season."""
    types = list(PREDICTED_GAME_TYPES)
    if args.seasons is not None:
        policy.check_holdout(args.seasons, args.unlock_holdout)
        with engine.connect() as conn:
            days = conn.execute(
                SEASON_DAYS_SQL, {"seasons": list(args.seasons), "game_types": types}
            ).scalars()
            return [(day, policy.backtest_as_of(day)) for day in days]
    with engine.connect() as conn:
        seasons = conn.execute(DAY_SEASONS_SQL, {"game_types": types, "day": args.date}).scalars()
        policy.check_holdout(sorted(seasons), args.unlock_holdout)
    as_of = now if args.origin == ORIGIN_LIVE else policy.backtest_as_of(args.date)
    return [(args.date, as_of)]


def cmd_features_compute(
    args: argparse.Namespace,
    *,
    policy: FeaturePolicy,
    engine: Engine | None = None,
    sources: Any = None,
    now: datetime | None = None,
) -> int:
    """presagio features compute: compute per day, store (or summarise), one output line."""
    del sources  # no external source is called
    if args.origin == ORIGIN_LIVE and args.date is None:
        print("presagio features: --origin live needs --date", file=sys.stderr)
        return EXIT_ARGS
    clock = now or datetime.now(UTC)
    try:
        if engine is None:
            from presagio.db.session import get_engine

            engine = get_engine()
        days = _days(args, engine, clock, policy)
    except policy.holdout_error as exc:
        print(f"presagio features: {exc}", file=sys.stderr)
        return EXIT_ARGS
    except Exception as exc:  # e.g. database unreachable: one line, no traceback
        logger.debug("features compute could not start", exc_info=True)
        print(f"presagio features: {error_line(exc)}", file=sys.stderr)
        return EXIT_FAILED
    rows: list[FeatureRow] = []
    computed_days = 0
    skipped: Counter[str] = Counter()
    try:
        with engine.connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            for day, as_of in days:
                if as_of > clock:
                    skipped["as_of_in_future"] += 1
                    continue
                day_rows = compute_day(conn, day, as_of, version=args.feature_version)
                kept = [r for r in day_rows if as_of < r.start_time_utc]
                skipped["started_before_as_of"] += len(day_rows) - len(kept)
                if kept:
                    computed_days += 1
                    rows.extend(kept)
            conn.rollback()
        store = None
        if args.store:
            with engine.begin() as conn:
                store = store_features(
                    conn, rows, args.origin, snapshot_kind=SNAPSHOT_KIND, computed_at=clock
                )
    except FeatureStoreError as exc:
        logger.error("store refused the features batch: %s", exc)
        print(f"presagio features: {error_line(exc)}", file=sys.stderr)
        return EXIT_FAILED
    except Exception as exc:  # e.g. database unreachable: one line, no traceback
        logger.debug("features compute failed", exc_info=True)
        print(f"presagio features: {error_line(exc)}", file=sys.stderr)
        return EXIT_FAILED
    for reason, n in sorted(skipped.items()):
        if n:
            logger.info("features: skipped %d (%s)", n, reason)
    dry = null_summary(rows) if store is None else None
    print(summary_line(args.feature_version, len(rows), computed_days, store, dry))
    if store is not None and store.differing:
        return EXIT_MISMATCH
    return EXIT_OK
