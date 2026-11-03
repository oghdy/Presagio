"""Check 17 feature_drift with the injected recompute function (P-035 criterion 6, D-056),
run in-process on the rolled-back league transaction, and the injection point itself."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import Connection, text

from presagio.evaluation.timing import backtest_as_of
from presagio.features.compute import compute_day
from presagio.features.recompute import recompute_stored
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
SETTINGS = CheckSettings(feature_recompute=recompute_stored)


def store_days(conn: Connection, *days: date) -> int:
    """Store the rows of the days; the number stored."""
    n = 0
    for day in days:
        rows = compute_day(conn, day, backtest_as_of(day))
        n += store_features(conn, rows, "backtest", computed_at=NOW, code_version="t").inserted
    return n


def drift(conn: Connection, **scope: date) -> CheckResult:
    """Run check 17 with the recompute function; its report row."""
    (result,) = run_checks(conn, checks=["feature_drift"], settings=SETTINGS, **scope).results
    return result


def test_stored_rows_recompute_to_themselves(conn: Connection) -> None:
    """PASS with n = the stored rows; a date scope narrows it."""
    assert drift(conn).status == SKIP  # nothing stored yet
    n = store_days(conn, date(2023, 12, 13), date(2024, 10, 22))
    result = drift(conn)
    assert (result.status, result.checked, result.failed) == (PASS, n, 0)
    assert result.note is None
    assert drift(conn, date_et=date(2024, 10, 22)).checked == 2


def test_an_edited_value_is_a_warning_with_the_row_and_key(conn: Connection) -> None:
    """Changing one stored value by SQL: WARN 1 with feature_id:game_id:fv1:<key>, the
    stored row untouched afterwards."""
    store_days(conn, date(2023, 12, 13))
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
    result = drift(conn)
    assert (result.status, result.checked, result.failed) == (WARN, 2, 1)
    assert result.examples == (f"{feature_id}:{game_id}:fv1:home_ortg_5",)
    value = conn.execute(
        text("SELECT \"values\"->>'home_ortg_5' FROM features WHERE feature_id = :f"),
        {"f": feature_id},
    ).scalar_one()
    assert value == "1.5"


def test_an_unknown_version_cannot_be_recomputed(conn: Connection) -> None:
    """A row under a version the manifest does not know is noted, not compared."""
    store_days(conn, date(2023, 12, 13))
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
    """recompute_stored: the values dict at the stored as_of; None for an unknown version."""
    rows = compute_day(conn, date(2023, 12, 13), backtest_as_of(date(2023, 12, 13)))
    row = rows[0]
    known = StoredFeature(1, row.game_id, row.as_of, "fv1", "daily", "backtest", {})
    assert recompute_stored(conn, known) == row.values
    unknown = StoredFeature(1, row.game_id, row.as_of, "fv9", "daily", "backtest", {})
    assert recompute_stored(conn, unknown) is None


def test_quality_cli_injects_recompute_stored_and_checks_never_import_features() -> None:
    """The injection is the one change in presagio.quality.cli; presagio.quality.checks
    knows nothing of the feature package."""
    from pathlib import Path

    cli_source = Path(checks.__file__).with_name("cli.py").read_text()
    assert "feature_recompute=recompute_stored" in cli_source
    assert "presagio.features" not in Path(checks.__file__).read_text()
