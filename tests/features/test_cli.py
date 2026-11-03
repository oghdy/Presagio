"""`presagio features compute` end to end on the temp league DB (P-035 criterion 5, 8 (k),
(l)): --store twice, --dry-run, the holdout gate, --date --origin live, exit codes 0/2/4,
and the quality checks 15 and 17 after a store."""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import Engine, text

from evaluation.league import LIVE_AVAILABLE, LIVE_NOT_FINAL, League
from presagio import cli
from presagio.features.manifest import FEATURE_VERSIONS

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
LINE = re.compile(
    r"^features fv1: computed (\d+) on (\d+) days; inserted (\d+), already stored (\d+), "
    r"differing (\d+)$"
)


def game_type(game_id: str) -> str:
    """The game type the database derives from the id (schema v1: third digit)."""
    return {"2": "regular", "4": "playoffs", "5": "playin", "6": "cup_final"}[game_id[2]]


@pytest.fixture(autouse=True)
def clean_features(engine: Engine) -> Iterator[None]:
    """--store commits to the league DB: empty the features table afterwards."""
    yield
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM features"))


def features(engine: Engine, *args: str, now: datetime = NOW) -> int:
    """Run `presagio features compute ...` against the league DB."""
    return cli.main(["features", "compute", *args], engine=engine, now=now, setup_logging=False)


def quality(engine: Engine, *args: str) -> int:
    """Run `presagio quality-check ...` against the league DB."""
    return cli.main(["quality-check", *args], engine=engine, setup_logging=False)


def counts(engine: Engine) -> dict[str, Any]:
    """Row count and a few aggregates of the features table."""
    with engine.connect() as conn:
        return dict(
            conn.execute(
                text(
                    "SELECT count(*) AS n, count(DISTINCT origin) AS origins, "
                    "count(*) FILTER (WHERE origin = 'live') AS live FROM features"
                )
            )
            .mappings()
            .one()
        )


def parse(out: str) -> tuple[int, ...]:
    """(computed, days, inserted, already stored, differing) of the output line."""
    line = out.strip().splitlines()[-1]
    match = LINE.match(line)
    assert match, line
    return tuple(int(x) for x in match.groups())


def test_store_twice(engine: Engine, league: League, capsys: pytest.CaptureFixture[str]) -> None:
    """(k) Both seasons stored once (exit 0), the rerun inserts 0 and finds every row
    equal; every stored row is backtest/daily with as_of 07:00 UTC before its tip-off."""
    code = features(engine, "--seasons", "2023-24,2024-25", "--store")
    first = parse(capsys.readouterr().out)
    assert code == 0
    computed, days, inserted, existing, differing = first
    assert computed == inserted > 300 and days > 100 and (existing, differing) == (0, 0)
    expected = sum(
        1 for g in league.games.values()
        if g["season"] in ("2023-24", "2024-25") and game_type(g["game_id"]) != "cup_final"
    )  # fmt: skip
    assert computed == expected
    assert counts(engine) == {"n": expected, "origins": 1, "live": 0}
    code = features(engine, "--seasons", "2023-24,2024-25", "--store")
    assert code == 0
    assert parse(capsys.readouterr().out) == (computed, days, 0, computed, 0)
    with engine.connect() as conn:
        bad = conn.execute(
            text(
                "SELECT count(*) FROM features f JOIN games g USING (game_id) WHERE "
                "f.as_of >= g.start_time_utc OR f.origin <> 'backtest' OR f.snapshot_kind <> "
                "'daily' OR f.as_of::time <> '07:00' OR f.computed_at <> :now OR "
                "f.feature_version <> 'fv1'"
            ),
            {"now": NOW},
        ).scalar_one()
    assert bad == 0


def test_dry_run_writes_nothing_and_summarises_keys(
    engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """--dry-run prints the computed count, the key count and the NULL share, writes 0."""
    code = features(engine, "--seasons", "2023-24", "--dry-run")
    out = capsys.readouterr().out
    assert code == 0
    assert re.search(
        r"^features fv1: computed \d+ on \d+ days; dry run \(nothing written\); ", out, re.M
    )
    assert f"keys {len(FEATURE_VERSIONS['fv1'].keys)}, null share " in out
    assert counts(engine)["n"] == 0


def test_holdout_season_is_refused_without_an_unlock(
    engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """(k) 2025-26 is refused (exit 2, 'holdout' in the message, nothing written); with a
    valid reason it is computed."""
    code = features(engine, "--seasons", "2025-26", "--store")
    captured = capsys.readouterr()
    assert code == 2 and "holdout" in captured.err and captured.out == ""
    assert counts(engine)["n"] == 0
    code = features(engine, "--seasons", "2023-24,2025-26", "--dry-run")
    assert code == 2 and "holdout" in capsys.readouterr().err
    code = features(
        engine, "--seasons", "2025-26", "--dry-run", "--unlock-holdout", "H-08 final model"
    )
    out = capsys.readouterr().out
    assert code == 0 and "computed 20 on 10 days" in out
    assert counts(engine)["n"] == 0


def test_live_date_stores_the_games_not_started_yet(
    engine: Engine, league: League, capsys: pytest.CaptureFixture[str]
) -> None:
    """(k) --date 2026-10-21 --origin live at 11:00 ET: the C-D game (19:30 ET) is stored
    with origin live, as_of = computed_at = now; after tip-off nothing is computed."""
    now = datetime(2026, 10, 21, 15, 0, tzinfo=UTC)
    code = features(engine, "--date", "2026-10-21", "--origin", "live", "--store", now=now)
    assert code == 0
    assert parse(capsys.readouterr().out) == (1, 1, 1, 0, 0)
    with engine.connect() as conn:
        row = conn.execute(text("SELECT * FROM features")).mappings().one()
    assert (row["game_id"], row["origin"], row["snapshot_kind"]) == (
        LIVE_AVAILABLE,
        "live",
        "daily",
    )
    assert row["as_of"] == now and row["computed_at"] == now
    assert row["as_of"] < league.games[LIVE_AVAILABLE]["start_time_utc"]
    code = features(engine, "--date", "2026-10-21", "--origin", "live", "--store", now=NOW)
    assert code == 0 and parse(capsys.readouterr().out) == (0, 0, 0, 0, 0)
    later = datetime(2026, 10, 23, 15, 0, tzinfo=UTC)
    code = features(engine, "--date", "2026-10-23", "--origin", "live", "--dry-run", now=later)
    assert code == 0 and "computed 1 on 1 days" in capsys.readouterr().out
    assert league.games[LIVE_NOT_FINAL]["status_code"] != 3
    assert counts(engine) == {"n": 1, "origins": 1, "live": 1}


def test_date_with_backtest_origin_uses_the_backtest_as_of(
    engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """--date without --origin live: as_of = 07:00 UTC of that date, origin backtest."""
    code = features(engine, "--date", "2023-12-13", "--store")
    assert code == 0 and parse(capsys.readouterr().out) == (2, 1, 2, 0, 0)
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT origin, as_of FROM features")).all()
    assert {r.origin for r in rows} == {"backtest"}
    assert {r.as_of for r in rows} == {datetime(2023, 12, 13, 7, 0, tzinfo=UTC)}


def test_live_without_date_is_an_argument_error(
    engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """--origin live needs --date (exit 2)."""
    code = features(engine, "--seasons", "2023-24", "--origin", "live", "--dry-run")
    assert code == 2 and "--date" in capsys.readouterr().err


def test_a_changed_stored_value_exits_4(engine: Engine, capsys: pytest.CaptureFixture[str]) -> None:
    """A stored row edited by SQL makes the rerun report differing 1 and exit 4; the row is
    not rewritten."""
    features(engine, "--date", "2023-12-13", "--store")
    capsys.readouterr()
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE features SET \"values\" = jsonb_set(\"values\", '{home_adv}', '9') "
                "WHERE feature_id = (SELECT min(feature_id) FROM features)"
            )
        )
    code = features(engine, "--date", "2023-12-13", "--store")
    assert code == 4 and parse(capsys.readouterr().out) == (2, 1, 0, 1, 1)
    with engine.connect() as conn:
        value = conn.execute(
            text("SELECT \"values\"->>'home_adv' FROM features ORDER BY feature_id LIMIT 1")
        ).scalar_one()
    assert value == "9"


def test_quality_checks_pass_after_a_store_and_see_an_edit(
    engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """(l) features_after_tipoff PASS and feature_drift PASS with n = stored rows; an edited
    value is a WARN with feature_id:game_id:fv1:<key>; an unknown version is noted."""
    features(engine, "--seasons", "2023-24", "--store")
    n = parse(capsys.readouterr().out)[2]
    code = quality(engine, "--check", "features_after_tipoff", "--check", "feature_drift")
    out = capsys.readouterr().out
    assert code == 0
    tipoff = next(line for line in out.splitlines() if " features_after_tipoff " in line)
    drift = next(line for line in out.splitlines() if " feature_drift " in line)
    assert " PASS " in tipoff and f" {n}       0 " in tipoff
    assert " PASS " in drift and f" {n}       0 " in drift
    with engine.begin() as conn:
        feature_id, game_id = conn.execute(
            text("SELECT feature_id, game_id FROM features ORDER BY feature_id LIMIT 1")
        ).one()
        conn.execute(
            text(
                "UPDATE features SET \"values\" = jsonb_set(\"values\", '{elo_home}', '1.0') "
                "WHERE feature_id = :f"
            ),
            {"f": feature_id},
        )
        conn.execute(
            text(
                "UPDATE features SET feature_version = 'fv0' WHERE feature_id = "
                "(SELECT max(feature_id) FROM features)"
            )
        )
    code = quality(engine, "--check", "feature_drift")
    out = capsys.readouterr().out
    assert code == 0  # WARN does not change the exit code
    drift = next(line for line in out.splitlines() if " feature_drift " in line)
    assert " WARN " in drift and f" {feature_id}:{game_id}:fv1:elo_home " in drift
    assert f" {n - 2}       1 " in drift
    assert "feature_drift: 1 row(s) could not be recomputed" in out


@pytest.mark.parametrize(
    "args",
    [
        ("--seasons", "2023-25", "--dry-run"),
        ("--date", "2023-13-01", "--dry-run"),
        ("--seasons", "2023-24"),
        ("--seasons", "2023-24", "--store", "--dry-run"),
        ("--seasons", "2023-24", "--date", "2023-12-13", "--dry-run"),
        ("--seasons", "2023-24", "--version", "fv9", "--dry-run"),
        ("--seasons", "2023-24", "--origin", "replay", "--dry-run"),
    ],
)
def test_bad_arguments_exit_64(
    engine: Engine, args: tuple[str, ...], capsys: pytest.CaptureFixture[str]
) -> None:
    """argparse-level errors keep the CLI's usage code 64."""
    with pytest.raises(SystemExit) as info:
        features(engine, *args)
    assert info.value.code == 64
    assert counts(engine)["n"] == 0


def test_unreachable_database_is_one_line_and_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    """A database error is reported in one line, exit 1, no traceback."""
    from presagio.db.session import make_engine

    dead = make_engine("postgresql+psycopg://x:y@127.0.0.1:9/none", connect_timeout=1)
    code = features(dead, "--seasons", "2023-24", "--dry-run")
    err = capsys.readouterr().err
    assert code == 1 and err.startswith("presagio features: ") and "Traceback" not in err
