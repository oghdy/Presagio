"""Time rules of the backtest (modeling design v1.2 1-3, 2-2, 2-3, 6-2).

D(x) = start_time_utc(x) as a US Eastern date. For a target game g on ET date d with
information boundary as_of, a finished game h may be used only when

    R1  D(h) < D(g)                                (strict: no other game of the same date)
    R2  start_time_utc(h) + lag <= as_of,  status_code(h) = 3,  both team stat rows exist

lag (Delta) is 3 hours by default (D-049 M-10, a setting). The backtest as_of of ET date d
is d 07:00 UTC (16:00 KST, independent of daylight saving time; D-049 M-1).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
# D-049 M-1: the backtest's daily snapshot is taken at 07:00 UTC of each ET date.
BACKTEST_AS_OF_TIME = time(7, 0, tzinfo=UTC)
# D-049 M-10: a result counts as known this long after tip-off (proposal value).
DEFAULT_AVAILABILITY_LAG = timedelta(hours=3)
FINAL_STATUS = 3


def et_date(moment: datetime) -> date:
    """D(x): the US Eastern calendar date of an aware UTC timestamp."""
    if moment.tzinfo is None:
        raise ValueError(f"need an aware timestamp, got naive {moment!r}")
    return moment.astimezone(EASTERN).date()


def backtest_as_of(day: date) -> datetime:
    """The backtest information boundary of ET date `day`: day 07:00 UTC."""
    return datetime.combine(day, BACKTEST_AS_OF_TIME)


def available_at(start_time_utc: datetime, lag: timedelta = DEFAULT_AVAILABILITY_LAG) -> datetime:
    """When a game's result counts as known in the backtest: tip-off + lag (R2)."""
    return start_time_utc + lag


def is_available(
    *,
    start_time_utc: datetime,
    status_code: int,
    has_stats: bool,
    target_date: date,
    as_of: datetime,
    lag: timedelta = DEFAULT_AVAILABILITY_LAG,
    game_et_date: date | None = None,
) -> bool:
    """R1 and R2: may a finished game h feed a prediction for ET date target_date at as_of?

    game_et_date is D(h) when the caller already has it (it must equal et_date(start)).
    """
    day = et_date(start_time_utc) if game_et_date is None else game_et_date
    return (
        day < target_date
        and available_at(start_time_utc, lag) <= as_of
        and status_code == FINAL_STATUS
        and has_stats
    )
