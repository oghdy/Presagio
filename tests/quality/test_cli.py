"""`presagio quality-check`: table and JSON output, exit codes 0/1/64 (criteria 2 and 3)."""

import json
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text

from presagio.cli import build_parser, main
from presagio.db.session import make_engine
from presagio.quality.checks import CHECK_NAMES, run_checks
from presagio.quality.report import render_text

from .conftest import Baseline


def run(capsys: pytest.CaptureFixture[str], *args: str, engine: Engine | None) -> tuple[int, str]:
    """Run the command in-process; return (exit code, stdout)."""
    code = main(["quality-check", *args], engine=engine, setup_logging=False)
    return code, capsys.readouterr().out


@pytest.fixture
def broken_game(engine: Engine, baseline: Baseline) -> Iterator[str]:
    """Commit one wrong home pts value (the CLI opens its own connection), then restore it."""
    game_id = baseline.regular_games[42]
    change = "UPDATE team_game_stats SET pts = pts {} 3 WHERE game_id = :g AND is_home"
    with engine.begin() as conn:
        conn.execute(text(change.format("+")), {"g": game_id})
    try:
        yield game_id
    finally:
        with engine.begin() as conn:
            conn.execute(text(change.format("-")), {"g": game_id})


def test_subcommand_is_registered_next_to_backfill() -> None:
    """presagio has both subcommands; -v stays before the subcommand (D-037)."""
    parser = build_parser()
    args = parser.parse_args(["-v", "quality-check", "--season", "2023-24"])
    assert (args.command, args.verbose, args.season) == ("quality-check", True, "2023-24")
    assert parser.parse_args(["backfill", "--season", "2023-24"]).command == "backfill"


def test_clean_data_prints_a_table_and_exits_0(
    capsys: pytest.CaptureFixture[str], engine: Engine
) -> None:
    """One row per check with target, counts and description; summary with exit code 0."""
    code, out = run(capsys, "--season", "2023-24", engine=engine)
    assert code == 0
    lines = out.splitlines()
    assert lines[0] == "Quality check: season 2023-24 (14 checks, read only)"
    assert lines[1].split() == [
        "#", "check", "target", "status", "passed", "failed", "examples", "description"
    ]  # fmt: skip
    for name in CHECK_NAMES:
        assert any(f" {name} " in line and " PASS " in line for line in lines), name
    row = next(line for line in lines if " team_rows_per_game " in line)
    assert "final games (regular, playin, playoffs); season 2023-24" in row
    assert " 1232       0 " in row  # passed, failed
    assert "each final game has 2 team stat rows, 1 home" in row
    assert "  5. regular_season_games: expected 1230 +/- 0; 2023-24: 1230 final" in out
    assert lines[-1] == "Summary: 14 pass, 0 fail, 0 warn, 0 error, 0 skip -> exit code 0"


def test_failure_exits_1_and_shows_the_game_id(
    capsys: pytest.CaptureFixture[str], engine: Engine, broken_game: str
) -> None:
    """Criterion 3: bad data is not an exception, it is exit code 1 with the failing ID."""
    code, out = run(capsys, engine=engine)
    assert code == 1
    row = next(line for line in out.splitlines() if " points_match_score " in line)
    assert " FAIL " in row
    assert f" 1231       1  {broken_game} " in row
    # The wrong pts also breaks pts = 2*fgm + fg3m + ftm for that row.
    arithmetic = next(line for line in out.splitlines() if " stat_arithmetic " in line)
    assert " FAIL  " in arithmetic and f"{broken_game}:" in arithmetic
    assert out.splitlines()[-1].endswith("2 fail, 0 warn, 0 error, 0 skip -> exit code 1")


def test_json_output_is_machine_readable(
    capsys: pytest.CaptureFixture[str], engine: Engine, broken_game: str
) -> None:
    """--json: scope, ok, exit_code, summary and one object per check with example IDs."""
    code, out = run(capsys, "--json", "--check", "points_match_score", engine=engine)
    data = json.loads(out)
    assert code == data["exit_code"] == 1
    assert data["ok"] is False
    assert data["scope"] == {"season": None, "date_et": None}
    assert data["summary"]["fail"] == 1
    (check,) = data["checks"]
    assert check["name"] == "points_match_score"
    assert (check["status"], check["passed"], check["failed"]) == ("fail", 1231, 1)
    assert check["examples"] == [broken_game]


def test_missing_season_fails(capsys: pytest.CaptureFixture[str], engine: Engine) -> None:
    """A season with no games at all is reported with its ID, exit 1."""
    code, out = run(capsys, "--season", "2024-25", "--check", "regular_season_games", engine=engine)
    assert code == 1
    assert " FAIL " in out
    assert "2024-25 (0)" in out


def test_more_than_five_failures_show_five_examples(engine: Engine) -> None:
    """Examples are capped at five; the table says how many more there are."""
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM team_game_stats"))
        report = run_checks(conn, checks=["team_rows_per_game"])
        conn.rollback()
    (result,) = report.results
    assert (result.failed, len(result.examples)) == (1232, 5)
    assert "(+1227 more)" in render_text(report)


def test_date_and_check_filters(capsys: pytest.CaptureFixture[str], engine: Engine) -> None:
    """--date narrows to one US Eastern date; --check picks checks; the count check skips."""
    code, out = run(
        capsys,
        "--date",
        "2023-10-24",
        "--check",
        "team_rows_per_game",
        "--check",
        "regular_season_games",
        engine=engine,
    )
    assert code == 0
    assert "(2 checks, read only)" in out
    assert " 15       0 " in next(line for line in out.splitlines() if "team_rows_per_game" in line)
    assert " SKIP " in next(line for line in out.splitlines() if "regular_season_games " in line)


@pytest.mark.parametrize(
    "args",
    [
        ["--season", "2023-25"],
        ["--season", "23-24"],
        ["--date", "2023-13-01"],
        ["--check", "no_such_check"],
        ["--games-tolerance", "-1"],
        ["--recent-days", "0"],
        ["--unknown"],
    ],
)
def test_bad_arguments_exit_64(args: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    """Usage errors use 64 (EX_USAGE), like backfill, and never touch the database."""
    with pytest.raises(SystemExit) as excinfo:
        main(["quality-check", *args], engine=None, setup_logging=False)
    assert excinfo.value.code == 64
    assert "error:" in capsys.readouterr().err


def test_unreachable_database_exits_1_without_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Nothing listens on port 1: one line on stderr, exit 1; --json still prints JSON."""
    nowhere = make_engine("postgresql+psycopg://user:pw@127.0.0.1:1/none", connect_timeout=1)
    assert main(["quality-check"], engine=nowhere, setup_logging=False) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("presagio: OperationalError")
    assert "Traceback" not in captured.err
    assert main(["quality-check", "--json"], engine=nowhere, setup_logging=False) == 1
    data = json.loads(capsys.readouterr().out)
    assert (data["ok"], data["exit_code"]) == (False, 1)
    assert data["error"].startswith("OperationalError")
