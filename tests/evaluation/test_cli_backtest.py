"""`presagio backtest run` end to end on the temp league DB (P-032 criterion 7, T10 at CLI
level; P-033 criteria 3, 4 and 5 (f): --store, exit codes 3 and 4)."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, text

from presagio import cli
from presagio.evaluation import cli as backtest_cli
from presagio.evaluation.store import StoreError
from presagio.evaluation.walkforward import LeakageGuardError

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
TABLES = ("games", "team_game_stats", "odds_events", "odds_snapshots", "ingestion_runs")
MODELING = ("model_versions", "predictions", "evaluations")


@pytest.fixture(autouse=True)
def quiet_logging() -> Iterator[None]:
    """main() is called with setup_logging=False, so nothing global to restore."""
    yield


@pytest.fixture
def clean_store(engine: Engine) -> Iterator[None]:
    """--store commits to the league DB: empty the modeling tables again afterwards."""
    yield
    with engine.begin() as conn:
        for table in reversed(MODELING):
            conn.execute(text(f"DELETE FROM {table}"))


def counts(engine: Engine, tables: tuple[str, ...] = TABLES + MODELING) -> dict[str, int]:
    """Row counts of the given tables."""
    with engine.connect() as conn:
        return {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one() for t in tables}


def backtest(engine: Engine, *args: str) -> int:
    """Run `presagio backtest run ...` against the league DB."""
    return cli.main(["backtest", "run", *args], engine=engine, now=NOW, setup_logging=False)


def test_dry_run_prints_the_tables_and_writes_nothing(
    engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """The criterion-7 command prints markdown and leaves every table as it was."""
    before = counts(engine)
    code = backtest(
        engine,
        "--experiment",
        "H-00",
        "--seasons",
        "2023-24,2024-25",
        "--dry-run",
        "--bootstrap",
        "50",
    )
    out = capsys.readouterr().out
    assert code == 0
    assert out.startswith("# Backtest H-00: dummy-constant")
    assert "| season | phase | set | n | accuracy | log loss [90%] |" in out
    assert "Dry run: nothing was written to the database." in out
    assert counts(engine) == before


def test_without_store_nothing_is_written_and_the_report_says_so(
    engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """A plain run (no --store, no --dry-run) writes nothing and points at --store."""
    before = counts(engine)
    code = backtest(engine, "--experiment", "B1", "--seasons", "2023-24", "--bootstrap", "20")
    out = capsys.readouterr().out
    assert code == 0 and out.startswith("# Backtest B1: B1 season win rate")
    assert "nothing was written to the database (pass --store" in out
    assert counts(engine) == before


def test_store_writes_once_and_a_rerun_changes_nothing(
    engine: Engine, capsys: pytest.CaptureFixture[str], clean_store: None
) -> None:
    """Criterion 3 at CLI level: `backtest run --experiment B0 --seasons 2023-24,2024-25
    --store` writes 1 configuration, 345 predictions and 345 evaluations; the same command
    again inserts nothing, reports 345 already stored and 0 differing, exit 0."""
    args = ("--experiment", "B0", "--seasons", "2023-24,2024-25", "--store", "--bootstrap", "20")
    assert backtest(engine, *args) == 0
    out = capsys.readouterr().out
    assert "Stored (--store): model_versions always_home_b0 v1 (model_version_id " in out
    assert "new); predictions inserted 345, already stored 0, differing 0; " in out
    assert "evaluations inserted 345, already stored 0, differing 0, " in out
    assert "not written for differing predictions 0; " in out
    assert "holdout rows not written: 0 predictions, 0 evaluations." in out
    after_first = counts(engine, MODELING)
    assert after_first == {"model_versions": 1, "predictions": 345, "evaluations": 345}
    assert backtest(engine, *args) == 0
    out = capsys.readouterr().out
    assert "existing); predictions inserted 0, already stored 345, differing 0; " in out
    assert "evaluations inserted 0, already stored 345, differing 0" in out
    assert counts(engine, MODELING) == after_first
    with engine.connect() as conn:
        families = conn.execute(
            text(
                "SELECT mv.model_family, count(p.*) FROM predictions p "
                "JOIN model_versions mv USING (model_version_id) GROUP BY 1"
            )
        ).all()
    assert families == [("baseline_home", 345)]


def test_a_changed_stored_value_is_reported_with_exit_4(
    engine: Engine, capsys: pytest.CaptureFixture[str], clean_store: None
) -> None:
    """Criterion 3: after a stored p_home is altered, the rerun reports 1 differing row,
    keeps the altered value (never overwrites) and exits 4."""
    args = ("--experiment", "B0", "--seasons", "2023-24", "--store", "--bootstrap", "20")
    assert backtest(engine, *args) == 0
    capsys.readouterr()
    with engine.begin() as conn:
        pid = conn.execute(
            text(
                "UPDATE predictions SET p_home = 0.1234 WHERE game_id = '0022300050' "
                "RETURNING prediction_id"
            )
        ).scalar_one()
    assert backtest(engine, *args) == backtest_cli.EXIT_MISMATCH == 4
    out = capsys.readouterr().out
    assert "predictions inserted 0, already stored 173, differing 1" in out
    assert "evaluations inserted 0, already stored 173, differing 0, " in out
    assert "not written for differing predictions 1; " in out
    assert "WARNING: 1 stored row(s) differ from this run and were NOT overwritten" in out
    assert "prediction ('0022300050'," in out and "p_home: Decimal('0.1234') ->" in out
    with engine.connect() as conn:
        kept = conn.execute(
            text("SELECT p_home FROM predictions WHERE prediction_id = :p"), {"p": pid}
        ).scalar_one()
    assert str(kept) == "0.1234"


def test_store_with_unlocked_holdout_records_the_reason(
    engine: Engine, capsys: pytest.CaptureFixture[str], clean_store: None
) -> None:
    """Criterion 4: a locked holdout with --store writes nothing (exit 64); unlocked, the
    2025-26 rows are written and model_versions carries the reason."""
    assert backtest(engine, "--experiment", "B0", "--seasons", "2024-25,2025-26", "--store") == 64
    capsys.readouterr()
    assert counts(engine, MODELING) == {"model_versions": 0, "predictions": 0, "evaluations": 0}
    code = backtest(
        engine,
        "--experiment",
        "B0",
        "--seasons",
        "2025-26",
        "--unlock-holdout",
        "H-08 final",
        "--store",
        "--bootstrap",
        "20",
    )
    out = capsys.readouterr().out
    assert code == 0 and "holdout unlock recorded: H-08 final" in out
    assert counts(engine, MODELING) == {"model_versions": 1, "predictions": 20, "evaluations": 20}
    with engine.connect() as conn:
        reason = conn.execute(
            text("SELECT holdout_unlocked_reason FROM model_versions")
        ).scalar_one()
    assert reason == "H-08 final"


def test_store_and_dry_run_are_exclusive_and_the_dummy_is_not_storable(
    engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """--store --dry-run is a usage error (64); so is --store with H-00 (no ModelSpec)."""
    with pytest.raises(SystemExit) as exc:
        backtest(engine, "--experiment", "B0", "--seasons", "2023-24", "--store", "--dry-run")
    assert exc.value.code == 64
    code = backtest(engine, "--experiment", "H-00", "--seasons", "2023-24", "--store")
    err = capsys.readouterr().err
    assert code == 64 and "not a storable model" in err
    assert counts(engine, MODELING) == {"model_versions": 0, "predictions": 0, "evaluations": 0}


def test_the_run_phase_is_a_read_only_transaction(
    engine: Engine, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The walk-forward and the scoring run under SET TRANSACTION READ ONLY: a write attempted
    on their connection fails (exit 1, the server's read-only message), even with --store."""

    def write_attempt(conn: object, *args: object, **kwargs: object) -> None:
        """Pretend a predictor tried to write."""
        conn.execute(text("UPDATE games SET status_text = status_text WHERE false"))  # type: ignore[attr-defined]

    monkeypatch.setattr(backtest_cli, "run_walk_forward", write_attempt)
    code = backtest(engine, "--experiment", "B0", "--seasons", "2023-24", "--store")
    captured = capsys.readouterr()
    assert code == 1 and "read-only transaction" in captured.err and captured.out == ""
    assert counts(engine, MODELING) == {"model_versions": 0, "predictions": 0, "evaluations": 0}


def test_store_error_exits_1_with_an_error_log_and_writes_nothing(
    engine: Engine,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A StoreError (configuration conflict) is exit 1, one stderr line, an ERROR log, and
    the store transaction is rolled back."""

    def refuse(*args: object, **kwargs: object) -> None:
        """Pretend the configuration row conflicts."""
        raise StoreError(
            "model_versions always_home_b0 v1 already exists with a different configuration"
        )

    monkeypatch.setattr(backtest_cli, "store_run", refuse)
    with caplog.at_level(logging.ERROR, logger="presagio.evaluation"):
        code = backtest(engine, "--experiment", "B0", "--seasons", "2023-24", "--store")
    captured = capsys.readouterr()
    assert code == 1 and captured.err.startswith("presagio backtest: StoreError: ")
    assert "Traceback" not in captured.err and captured.out == ""
    assert any(
        r.levelno == logging.ERROR and "store refused the run" in r.getMessage()
        for r in caplog.records
    )
    assert counts(engine, MODELING) == {"model_versions": 0, "predictions": 0, "evaluations": 0}


def test_leakage_guard_exits_3_with_an_error_log_and_no_traceback(
    engine: Engine,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Criterion 5 (f): a LeakageGuardError is exit 3 (not 1), one stderr line, ERROR log."""

    def explode(*args: object, **kwargs: object) -> None:
        """Pretend a training source lied."""
        raise LeakageGuardError("training game 0022300001 self-reports a lie")

    monkeypatch.setattr(backtest_cli, "run_walk_forward", explode)
    with caplog.at_level(logging.ERROR, logger="presagio.evaluation"):
        code = backtest(engine, "--experiment", "B0", "--seasons", "2023-24", "--store")
    captured = capsys.readouterr()
    assert code == backtest_cli.EXIT_LEAKAGE == 3
    assert captured.err.startswith("presagio backtest: leakage guard: LeakageGuardError: ")
    assert "Traceback" not in captured.err and captured.out == ""
    assert any(
        r.levelno == logging.ERROR and "leakage guard stopped the backtest" in r.getMessage()
        for r in caplog.records
    )
    assert counts(engine, MODELING) == {"model_versions": 0, "predictions": 0, "evaluations": 0}


def test_holdout_season_is_refused_with_exit_64(
    engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """T10: --seasons 2025-26 without --unlock-holdout -> exit 64, 'holdout' on stderr."""
    code = backtest(engine, "--experiment", "H-00", "--seasons", "2024-25,2025-26")
    captured = capsys.readouterr()
    assert code == 64 and "holdout" in captured.err and captured.out == ""


def test_unlock_holdout_runs_and_reports_the_reason(
    engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """T10: with --unlock-holdout "H-08 final" the run happens and the reason is reported."""
    code = backtest(
        engine,
        "--experiment",
        "H-00",
        "--seasons",
        "2025-26",
        "--unlock-holdout",
        "H-08 final",
        "--bootstrap",
        "20",
    )
    assert code == 0
    assert "Holdout UNLOCKED for 2025-26: H-08 final" in capsys.readouterr().out


@pytest.mark.parametrize(
    "args",
    [
        ("--experiment", "H-99", "--seasons", "2023-24"),
        ("--experiment", "H-00", "--seasons", "2023-25"),
        ("--experiment", "H-00", "--seasons", "2023-24,2023-24"),
        ("--experiment", "H-00"),
        ("--experiment", "H-00", "--seasons", "2023-24", "--refit-every-days", "0"),
        ("--experiment", "H-00", "--seasons", "2023-24", "--availability-lag-hours", "-1"),
    ],
)
def test_bad_arguments_exit_64(engine: Engine, args: tuple[str, ...]) -> None:
    """Unknown experiment, bad or duplicate season, missing seasons, bad settings."""
    with pytest.raises(SystemExit) as exc:
        backtest(engine, *args)
    assert exc.value.code == 64


def test_unreachable_database_is_one_line_and_exit_1(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A database error is reported in one line, exit 1, no traceback."""
    from presagio.db.session import make_engine

    dead = make_engine("postgresql+psycopg://x:y@127.0.0.1:9/none", connect_timeout=1)
    code = backtest(dead, "--experiment", "H-00", "--seasons", "2023-24")
    err = capsys.readouterr().err
    assert code == 1 and err.startswith("presagio backtest: ") and "Traceback" not in err
