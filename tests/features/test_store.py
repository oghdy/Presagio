"""store_features on the temp league DB (P-035 criterion 4, 8 (h)-(j); design 8-1, R4,
T11, T12): append only, differences reported with examples and never written, the R4 and
clock guards, the live cap enforced by the database CHECK."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Connection, event, text

from evaluation.league import LIVE_AVAILABLE
from presagio.evaluation.timing import backtest_as_of
from presagio.features import store as store_module
from presagio.features.compute import FeatureRow, compute_day
from presagio.features.store import FeatureStoreError, first_difference, store_features

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
CODE = "test1234"
DAY = date(2023, 12, 13)


def rows_for(conn: Connection, day: date = DAY) -> list[FeatureRow]:
    """The day's rows at the backtest as_of."""
    rows = compute_day(conn, day, backtest_as_of(day))
    assert len(rows) == 2
    return rows


def count(conn: Connection) -> int:
    """features row count."""
    return conn.execute(text("SELECT count(*) FROM features")).scalar_one()


def stored(conn: Connection) -> list[Any]:
    """Every features row in id order."""
    return conn.execute(text("SELECT * FROM features ORDER BY feature_id")).mappings().all()


def test_store_writes_the_rows_with_the_batch_metadata(conn: Connection) -> None:
    """One row per FeatureRow: key, kind, origin, values and fingerprint round-tripped
    through jsonb unchanged, computed_at from the Python clock, code_version."""
    rows = rows_for(conn)
    summary = store_features(conn, rows, "backtest", computed_at=NOW, code_version=CODE)
    assert (summary.inserted, summary.already_stored, summary.differing) == (2, 0, 0)
    assert summary.examples == [] and summary.total == 2
    saved = stored(conn)
    assert [r["game_id"] for r in saved] == [r.game_id for r in rows]
    for row, got in zip(rows, saved, strict=True):
        assert got["as_of"] == row.as_of and got["feature_version"] == "fv1"
        assert (got["snapshot_kind"], got["origin"]) == ("daily", "backtest")
        assert got["values"] == row.values and got["input_fingerprint"] == row.input_fingerprint
        assert set(got["values"]) == set(row.values)  # jsonb keeps no key order
        assert got["computed_at"] == NOW and got["code_version"] == CODE


def test_storing_twice_changes_nothing(conn: Connection) -> None:
    """T12 / (h): the second store inserts 0, finds every row equal, reports no difference."""
    rows = rows_for(conn)
    store_features(conn, rows, "backtest", computed_at=NOW, code_version=CODE)
    before = stored(conn)
    again = store_features(
        conn, rows, "backtest", computed_at=NOW + timedelta(days=1), code_version="x"
    )
    assert (again.inserted, again.already_stored, again.differing) == (0, 2, 0)
    assert stored(conn) == before


def test_different_values_are_reported_and_never_written(conn: Connection) -> None:
    """T11 / (i): the same key with other values is counted as differing with the example
    feature_id:game_id:<first differing key>; the stored values stay as they were."""
    rows = rows_for(conn)
    store_features(conn, rows, "backtest", computed_at=NOW, code_version=CODE)
    first_id = stored(conn)[0]["feature_id"]
    changed = replace(rows[0], values={**rows[0].values, "home_adv": 7, "elo_prob": 0.5})
    summary = store_features(
        conn, [changed, rows[1]], "backtest", computed_at=NOW, code_version=CODE
    )
    assert (summary.inserted, summary.already_stored, summary.differing) == (0, 1, 1)
    assert summary.examples == [f"{first_id}:{rows[0].game_id}:elo_prob"]  # first key sorted
    assert count(conn) == 2
    saved = stored(conn)[0]
    assert saved["values"] == rows[0].values and saved["values"]["home_adv"] == 1
    missing = replace(rows[0], values={k: v for k, v in rows[0].values.items() if k != "home_adv"})
    summary = store_features(conn, [missing], "backtest", computed_at=NOW, code_version=CODE)
    assert summary.differing == 1 and summary.examples == [f"{first_id}:{rows[0].game_id}:home_adv"]


def test_first_difference_is_the_first_sorted_key() -> None:
    """Equal dicts give None; otherwise the smallest differing key (missing keys count)."""
    assert first_difference({"a": 1, "b": None}, {"a": 1, "b": None}) is None
    assert first_difference({"a": 1, "b": 2}, {"a": 1, "b": 3}) == "b"
    assert first_difference({"a": 1}, {"a": 1, "b": 3}) == "b"
    assert first_difference({"z": 1, "a": 2}, {"z": 2, "a": 3}) == "a"


def test_examples_stop_at_five(conn: Connection, league: Any) -> None:
    """At most five examples are kept while every differing row is counted."""
    rows: list[FeatureRow] = []
    for day in (date(2023, 12, 13), date(2023, 12, 15), date(2023, 12, 17)):
        rows += rows_for(conn, day)
    store_features(conn, rows, "backtest", computed_at=NOW, code_version=CODE)
    changed = [replace(r, values={**r.values, "home_adv": 3}) for r in rows]
    summary = store_features(conn, changed, "backtest", computed_at=NOW, code_version=CODE)
    assert summary.differing == 6 and len(summary.examples) == 5


def test_as_of_at_or_after_tipoff_refuses_the_whole_batch(conn: Connection) -> None:
    """(j) Rule R4: one row with as_of = start_time_utc makes the batch a FeatureStoreError
    and nothing is written, not even the good row."""
    rows = rows_for(conn)
    bad = replace(rows[1], as_of=rows[1].start_time_utc)
    with pytest.raises(FeatureStoreError, match="R4"):
        store_features(conn, [rows[0], bad], "backtest", computed_at=NOW)
    assert count(conn) == 0
    after = replace(rows[1], as_of=rows[1].start_time_utc + timedelta(seconds=1))
    with pytest.raises(FeatureStoreError, match="R4"):
        store_features(conn, [after], "backtest", computed_at=NOW)
    assert count(conn) == 0


def test_as_of_after_computed_at_is_refused(conn: Connection) -> None:
    """as_of <= computed_at on the Python clock; a naive computed_at is refused too."""
    rows = rows_for(conn)
    with pytest.raises(FeatureStoreError, match="computed_at"):
        store_features(conn, rows, "backtest", computed_at=rows[0].as_of - timedelta(seconds=1))
    with pytest.raises(FeatureStoreError, match="aware"):
        store_features(conn, rows, "backtest", computed_at=datetime(2026, 1, 1))
    assert count(conn) == 0


@pytest.mark.parametrize(
    ("origin", "kind", "message"),
    [("replay", "daily", "origin"), ("backtest", "weekly", "snapshot_kind")],
)
def test_bad_origin_or_snapshot_kind(
    conn: Connection, origin: str, kind: str, message: str
) -> None:
    """Only ORIGINS and SNAPSHOT_KINDS are accepted."""
    rows = rows_for(conn)
    with pytest.raises(FeatureStoreError, match=message):
        store_features(conn, rows, origin, snapshot_kind=kind, computed_at=NOW)
    assert count(conn) == 0


def test_live_rows_within_two_hours_are_stored_and_later_ones_refused(
    conn: Connection,
) -> None:
    """(j) origin live: computed_at - as_of <= 2 h passes; 2 h + 1 s is refused by the DB
    CHECK live_computed_within_2h, reported as a FeatureStoreError, nothing written, and
    the transaction stays usable (savepoint)."""
    day = date(2026, 10, 21)
    boundary = datetime(2026, 10, 21, 15, 0, tzinfo=UTC)  # 11:00 ET, before the 19:30 ET tip
    rows = compute_day(conn, day, boundary)
    assert [r.game_id for r in rows] == [LIVE_AVAILABLE]
    with pytest.raises(FeatureStoreError, match="live_computed_within_2h"):
        store_features(conn, rows, "live", computed_at=boundary + timedelta(hours=2, seconds=1))
    assert count(conn) == 0
    summary = store_features(conn, rows, "live", computed_at=boundary + timedelta(hours=2))
    assert summary.inserted == 1
    saved = stored(conn)[0]
    assert (saved["origin"], saved["as_of"]) == ("live", boundary)
    backtest = store_features(conn, rows, "backtest", computed_at=NOW)  # same key: not rewritten
    assert (backtest.inserted, backtest.already_stored) == (0, 1)


def test_code_version_falls_back_to_unknown(
    conn: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a git checkout or an installed package the row says 'unknown'."""
    monkeypatch.setattr("presagio.features.store.current_code_version", lambda: None)
    store_features(conn, rows_for(conn), "backtest", computed_at=NOW)
    assert {r["code_version"] for r in stored(conn)} == {"unknown"}


def test_empty_batch_is_a_no_op(conn: Connection) -> None:
    """Nothing to store: zero counts, no error."""
    summary = store_features(conn, [], "backtest", computed_at=NOW)
    assert (summary.inserted, summary.already_stored, summary.differing) == (0, 0, 0)
    assert count(conn) == 0


def test_existing_rows_are_read_once_per_chunk(
    conn: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rerun reads the stored rows one chunk at a time (review finding 1): with 6 rows
    and a chunk of 2, the second store issues 3 SELECTs on features and 3 INSERTs, never
    one query per row; the counts and examples are the same as before."""
    rows: list[FeatureRow] = []
    for day in (date(2023, 12, 13), date(2023, 12, 15), date(2023, 12, 17)):
        rows += rows_for(conn, day)
    monkeypatch.setattr(store_module, "CHUNK", 2)
    statements: list[str] = []

    def record(_conn: Any, _cursor: Any, statement: str, *_: Any) -> None:
        """Keep every SQL text sent on this connection."""
        statements.append(statement)

    event.listen(conn, "before_cursor_execute", record)
    try:
        first = store_features(conn, rows, "backtest", computed_at=NOW, code_version=CODE)
        assert (first.inserted, first.already_stored, first.differing) == (6, 0, 0)
        assert sum("INSERT INTO features" in s for s in statements) == 3
        assert sum("FROM features" in s for s in statements) == 0  # nothing to compare
        statements.clear()
        changed = [rows[0], *(replace(r, values={**r.values, "home_adv": 3}) for r in rows[1:])]
        second = store_features(conn, changed, "backtest", computed_at=NOW, code_version=CODE)
        assert (second.inserted, second.already_stored, second.differing) == (0, 1, 5)
        assert len(second.examples) == 5
        assert sum("INSERT INTO features" in s for s in statements) == 3
        assert sum("FROM features" in s for s in statements) == 3  # one per chunk of 2
        assert all("WHERE game_id = ANY" in s for s in statements if "FROM features" in s)
    finally:
        event.remove(conn, "before_cursor_execute", record)
