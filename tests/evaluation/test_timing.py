"""Time rules R1, R2 and the backtest as_of (design 2-2, 1-3)."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from presagio.evaluation.timing import (
    DEFAULT_AVAILABILITY_LAG,
    available_at,
    backtest_as_of,
    et_date,
    is_available,
)

ET = ZoneInfo("America/New_York")


def test_backtest_as_of_is_07_utc_in_summer_and_winter() -> None:
    """07:00 UTC = 16:00 KST every day; 03:00 EDT in October, 02:00 EST in January."""
    october = backtest_as_of(date(2026, 10, 23))
    january = backtest_as_of(date(2027, 1, 15))
    assert october == datetime(2026, 10, 23, 7, 0, tzinfo=UTC)
    assert october.astimezone(ET).hour == 3 and january.astimezone(ET).hour == 2
    assert et_date(october) == date(2026, 10, 23)


def test_et_date_of_a_late_game_is_the_previous_utc_day() -> None:
    """22:30 ET on 1 Dec is 03:30 UTC on 2 Dec but ET date 1 Dec."""
    start = datetime(2023, 12, 1, 22, 30, tzinfo=ET).astimezone(UTC)
    assert start.date() == date(2023, 12, 2) and et_date(start) == date(2023, 12, 1)
    with pytest.raises(ValueError):
        et_date(datetime(2023, 12, 1))


def test_r1_excludes_the_same_et_date_even_when_finished() -> None:
    """A 12:00 ET game is long over by the target day's evening but D(h) = D(g)."""
    start = datetime(2024, 1, 15, 12, 0, tzinfo=ET).astimezone(UTC)
    assert not is_available(
        start_time_utc=start,
        status_code=3,
        has_stats=True,
        target_date=date(2024, 1, 15),
        as_of=datetime(2024, 1, 16, 0, 0, tzinfo=UTC),
    )
    assert is_available(
        start_time_utc=start,
        status_code=3,
        has_stats=True,
        target_date=date(2024, 1, 16),
        as_of=backtest_as_of(date(2024, 1, 16)),
    )


def test_t4_availability_lag_has_effect() -> None:
    """T4: h at d10 22:30 ET, as_of = d11 01:00 ET (start + 2.5 h). Delta 3 h -> excluded,
    Delta 2 h -> included."""
    start = datetime(2024, 1, 10, 22, 30, tzinfo=ET).astimezone(UTC)
    as_of = datetime(2024, 1, 11, 1, 0, tzinfo=ET).astimezone(UTC)
    common = {"start_time_utc": start, "status_code": 3, "has_stats": True}
    target = date(2024, 1, 11)
    assert not is_available(**common, target_date=target, as_of=as_of)
    assert is_available(**common, target_date=target, as_of=as_of, lag=timedelta(hours=2))
    assert available_at(start) == start + DEFAULT_AVAILABILITY_LAG


def test_r2_boundary_is_inclusive_and_needs_final_status_and_stats() -> None:
    """start + lag == as_of is available; one second later is not; status and stats matter."""
    as_of = backtest_as_of(date(2024, 1, 11))
    start = as_of - DEFAULT_AVAILABILITY_LAG
    common = {"target_date": date(2024, 1, 11), "as_of": as_of}
    assert is_available(start_time_utc=start, status_code=3, has_stats=True, **common)
    later = start + timedelta(seconds=1)
    assert not is_available(start_time_utc=later, status_code=3, has_stats=True, **common)
    assert not is_available(start_time_utc=start, status_code=2, has_stats=True, **common)
    assert not is_available(start_time_utc=start, status_code=3, has_stats=False, **common)
