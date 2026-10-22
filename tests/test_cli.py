"""presagio CLI without a database: logging levels, argument errors, the entry point."""

import logging
import subprocess
import sys
from collections.abc import Iterator
from importlib import metadata

import pytest

from presagio import cli

WATCHED = ("", "presagio", "presagio.ingestion.backfill", "httpx", "httpcore")


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    """main() reconfigures logging globally; put levels and root handlers back afterwards."""
    loggers = [logging.getLogger(name) for name in WATCHED]
    levels = [lg.level for lg in loggers]
    handlers = logging.getLogger().handlers[:]
    yield
    for lg, level in zip(loggers, levels, strict=True):
        lg.setLevel(level)
    logging.getLogger().handlers[:] = handlers


@pytest.fixture
def no_backfill(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Replace the backfill command so main() only parses arguments and sets up logging."""
    calls: list[object] = []

    def fake(args: object, request: object, **kwargs: object) -> int:
        """Record the call instead of touching a database."""
        calls.append(request)
        return 0

    monkeypatch.setattr(cli, "cmd_backfill", fake)
    return calls


@pytest.mark.parametrize("verbose", [[], ["-v"]], ids=["default", "verbose"])
def test_httpx_logger_stays_at_warning(no_backfill, capsys, verbose) -> None:
    """Even with -v, httpx/httpcore never log at INFO (URLs carry the API key, D-031)."""
    logging.getLogger("httpx").setLevel(logging.DEBUG)  # someone lowered it before main()
    assert cli.main([*verbose, "backfill", "--season", "2025-26", "--dry-run"]) == 0
    for name in cli.QUIET_LOGGERS:
        assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING
    ours = logging.getLogger("presagio.ingestion.backfill").getEffectiveLevel()
    assert ours == (logging.DEBUG if verbose else logging.INFO)
    logging.getLogger("httpx").info("HTTP Request: GET https://x.test/?apiKey=SECRET-KEY-123")
    logging.getLogger("presagio.ingestion.backfill").info("progress line")
    err = capsys.readouterr().err
    assert "SECRET-KEY-123" not in err and "progress line" in err
    assert len(no_backfill) == 1


@pytest.mark.parametrize(
    "argv",
    [
        ["backfill"],  # neither --season nor --from
        ["backfill", "--season", "2025-27"],
        ["backfill", "--from", "2024-01-01"],  # --to missing
        ["backfill", "--season", "2025-26", "--to", "2024-01-01"],
        ["backfill", "--season", "2025-26", "--from", "2024-01-01", "--to", "2024-01-31"],
        ["backfill", "--from", "2024-02-01", "--to", "2024-01-01"],
        ["backfill", "--from", "2024-13-01", "--to", "2024-01-01"],
        ["backfill", "--season", "2025-26", "--season-type", "preseason"],
    ],
)
def test_bad_arguments_exit_64_not_2(no_backfill, capsys, argv) -> None:
    """Usage errors exit 64 (EX_USAGE); 2 is reserved for "blocked by the source"."""
    with pytest.raises(SystemExit) as caught:
        cli.main(argv)
    assert caught.value.code == cli.EXIT_USAGE == 64
    assert "error:" in capsys.readouterr().err
    assert no_backfill == []


def test_arguments_become_a_request(no_backfill) -> None:
    """Repeated --season and --season-type are deduplicated and ordered."""
    argv = ["backfill", "--season", "2025-26", "--season", "2023-24", "--season-type", "playoffs"]
    assert cli.main([*argv, "--force"], setup_logging=False) == 0
    request = no_backfill[0]
    assert request.seasons == ("2023-24", "2025-26")
    assert request.season_types == ("playoffs",)
    assert request.force is True


def test_console_script_is_registered() -> None:
    """pyproject [project.scripts]: `presagio` runs presagio.cli:main."""
    (entry,) = metadata.entry_points(group="console_scripts", name="presagio")
    assert entry.value == "presagio.cli:main"


def test_help_runs_as_a_module() -> None:
    """`python -m presagio.cli backfill --help` works without a database or network."""
    completed = subprocess.run(
        [sys.executable, "-m", "presagio.cli", "backfill", "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "--dry-run" in completed.stdout and "--from" in completed.stdout
