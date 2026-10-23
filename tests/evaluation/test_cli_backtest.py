"""`presagio backtest run` end to end on the temp league DB (criterion 7, T10 at CLI level)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, text

from presagio import cli

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
TABLES = ("games", "team_game_stats", "odds_events", "odds_snapshots", "ingestion_runs")


@pytest.fixture(autouse=True)
def quiet_logging() -> Iterator[None]:
    """main() is called with setup_logging=False, so nothing global to restore."""
    yield


def counts(engine: Engine) -> dict[str, int]:
    """Row counts of the tables the command must not touch."""
    with engine.connect() as conn:
        return {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one() for t in TABLES}


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
    assert "Dry run: nothing was written" in out
    assert counts(engine) == before


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
