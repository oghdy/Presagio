"""Check 17 feature_drift with the injected recompute function (P-035 criterion 6, D-056),
run in-process on the rolled-back league transaction; the cached FeatureRecomputer (review
recommendation: one compute_day per (D(g), as_of, version) per transaction) and the plain
recompute_stored agree; the injection point itself."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, event, text

from presagio.evaluation.timing import backtest_as_of
from presagio.features.compute import FeatureRow, compute_day, compute_features
from presagio.features.recompute import FeatureRecomputer, recompute_stored
from presagio.features.store import store_features
from presagio.quality import checks
from presagio.quality.checks import (
    PASS,
    SKIP,
    WARN,
    CheckResult,
    CheckSettings,
    StoredFeature,
    run_checks,
)

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
DAY = date(2023, 12, 13)


def store_days(conn: Connection, *days: date) -> int:
    """Store the rows of the days; the number stored."""
    n = 0
    for day in days:
        rows = compute_day(conn, day, backtest_as_of(day))
        n += store_features(conn, rows, "backtest", computed_at=NOW, code_version="t").inserted
    return n


def drift(conn: Connection, recompute: Any = None, **scope: date) -> CheckResult:
    """Run check 17 with a recompute function (a fresh cached recomputer by default)."""
    settings = CheckSettings(feature_recompute=recompute or FeatureRecomputer())
    (result,) = run_checks(conn, checks=["feature_drift"], settings=settings, **scope).results
    return result


def as_stored(row: FeatureRow, version: str = "fv1") -> StoredFeature:
    """A StoredFeature for a computed row (feature_id and values do not matter here)."""
    return StoredFeature(1, row.game_id, row.as_of, version, "daily", "backtest", {})


@pytest.mark.parametrize("recompute", [recompute_stored, FeatureRecomputer()])
def test_stored_rows_recompute_to_themselves(conn: Connection, recompute: Any) -> None:
    """PASS with n = the stored rows, with either recompute function; a date scope
    narrows it."""
    assert drift(conn, recompute).status == SKIP  # nothing stored yet
    n = store_days(conn, DAY, date(2024, 10, 22))
    result = drift(conn, recompute)
    assert (result.status, result.checked, result.failed) == (PASS, n, 0)
    assert result.note is None
    assert drift(conn, recompute, date_et=date(2024, 10, 22)).checked == 2


@pytest.mark.parametrize("recompute", [recompute_stored, FeatureRecomputer()])
def test_an_edited_value_is_a_warning_with_the_row_and_key(
    conn: Connection, recompute: Any
) -> None:
    """Changing one stored value by SQL: WARN 1 with feature_id:game_id:fv1:<key>, the
    stored row untouched afterwards."""
    store_days(conn, DAY)
    feature_id, game_id = conn.execute(
        text("SELECT feature_id, game_id FROM features ORDER BY feature_id LIMIT 1")
    ).one()
    conn.execute(
        text(
            "UPDATE features SET \"values\" = jsonb_set(\"values\", '{home_ortg_5}', '1.5') "
            "WHERE feature_id = :f"
        ),
        {"f": feature_id},
    )
    result = drift(conn, recompute)
    assert (result.status, result.checked, result.failed) == (WARN, 2, 1)
    assert result.examples == (f"{feature_id}:{game_id}:fv1:home_ortg_5",)
    value = conn.execute(
        text("SELECT \"values\"->>'home_ortg_5' FROM features WHERE feature_id = :f"),
        {"f": feature_id},
    ).scalar_one()
    assert value == "1.5"


def test_an_unknown_version_cannot_be_recomputed(conn: Connection) -> None:
    """A row under a version the manifest does not know is noted, not compared."""
    store_days(conn, DAY)
    conn.execute(
        text(
            "UPDATE features SET feature_version = 'fv0' WHERE feature_id = "
            "(SELECT min(feature_id) FROM features)"
        )
    )
    result = drift(conn)
    assert (result.status, result.checked, result.failed) == (PASS, 1, 0)
    assert result.note == "1 row(s) could not be recomputed"
    conn.execute(text("UPDATE features SET feature_version = 'fv0'"))
    nothing = drift(conn)
    assert (nothing.status, nothing.note) == (SKIP, "none of 2 stored row(s) could be recomputed")


def test_recompute_stored_returns_values_or_none(conn: Connection) -> None:
    """recompute_stored: the values dict at the stored as_of; None for an unknown version;
    the cached recomputer gives exactly the same for every row of two days."""
    cached = FeatureRecomputer()
    for day in (DAY, date(2024, 10, 22)):
        for row in compute_day(conn, day, backtest_as_of(day)):
            assert recompute_stored(conn, as_stored(row)) == row.values
            assert cached(conn, as_stored(row)) == row.values
            assert cached(conn, as_stored(row, "fv9")) is None
            assert recompute_stored(conn, as_stored(row, "fv9")) is None


def test_cached_recomputer_computes_a_day_once(conn: Connection) -> None:
    """Two rows of the same (D(g), as_of): the second costs one statement (the game's
    start) instead of the whole history; a different as_of is computed again."""
    rows = compute_day(conn, DAY, backtest_as_of(DAY))
    assert len(rows) == 2
    statements: list[str] = []

    def record(_conn: Any, _cursor: Any, statement: str, *_: Any) -> None:
        """Count every SQL text sent on this connection."""
        statements.append(statement)

    later = backtest_as_of(DAY).replace(hour=9)
    expected = compute_features(conn, rows[0].game_id, later).values
    event.listen(conn, "before_cursor_execute", record)
    try:
        cached = FeatureRecomputer()
        assert cached(conn, as_stored(rows[0])) == rows[0].values
        per_day = len(statements)
        assert per_day >= 5  # the start lookup plus compute_day's statements
        assert cached(conn, as_stored(rows[1])) == rows[1].values
        assert len(statements) == per_day + 1
        live = StoredFeature(1, rows[0].game_id, later, "fv1", "daily", "live", {})
        assert cached(conn, live) == expected
        assert len(statements) == 2 * per_day + 1  # another as_of: computed again
        assert cached(conn, as_stored(rows[0])) == rows[0].values  # the first day is kept
        assert cached(conn, live) == expected
        assert len(statements) == 2 * per_day + 3
    finally:
        event.remove(conn, "before_cursor_execute", record)


def test_cache_is_scoped_to_the_transaction(engine: Engine, league: Any) -> None:
    """One recomputer, two connections: a change seen only inside connection A's
    transaction changes A's recomputed values, while B (committed data) and a later
    transaction on A see the original values — no entry leaks across transactions."""
    cached = FeatureRecomputer()
    day = date(2023, 12, 13)
    with engine.connect() as a, engine.connect() as b:
        ta = a.begin()
        rows = compute_day(a, day, backtest_as_of(day))
        original = {r.game_id: r.values for r in rows}
        assert {r.game_id: cached(b, as_stored(r)) for r in rows} == original
        earlier = sorted(league.ids("2023-24"))[0]
        a.execute(
            text("UPDATE team_game_stats SET pts = pts + 50 WHERE game_id = :g AND is_home"),
            {"g": earlier},
        )
        changed = {r.game_id: cached(a, as_stored(r)) for r in rows}
        assert changed != original
        assert changed == {r.game_id: recompute_stored(a, as_stored(r)) for r in rows}
        assert {r.game_id: cached(b, as_stored(r)) for r in rows} == original
        ta.rollback()
        with a.begin():
            assert {r.game_id: cached(a, as_stored(r)) for r in rows} == original


def test_quality_cli_injects_the_recomputer_and_checks_never_import_features() -> None:
    """The injection is the one change in presagio.quality.cli; presagio.quality.checks
    knows nothing of the feature package."""
    cli_source = Path(checks.__file__).with_name("cli.py").read_text()
    assert "feature_recompute=FeatureRecomputer()" in cli_source
    assert "presagio.features" not in Path(checks.__file__).read_text()
