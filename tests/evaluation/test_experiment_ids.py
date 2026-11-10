"""Parameterised experiment ids and argument errors of run / compare / grid (P-037 A2, A6)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, text

from presagio import cli
from presagio.evaluation.experiments import (
    ExperimentIdError,
    resolve_experiment,
    split_experiment_list,
)

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
MODELING = ("model_versions", "predictions", "evaluations")


@pytest.mark.parametrize(
    ("experiment_id", "canonical", "version"),
    [
        ("B2", "B2", "1"),
        ("B2[k=25,hca=75,carry=0.9]", "B2[k=25,hca=75,carry=0.9]", "1-k25-hca75-c0.9"),
        ("B2[k=20,hca=100,carry=0.75]", "B2[k=20,hca=100,carry=0.75]", "1"),
        ("B2[k=20.0]", "B2[k=20]", "1"),
        ("B2[ k = 25 ]", "B2[k=25]", "1-k25-hca100-c0.75"),
        ("B2[carry=0.6,k=10]", "B2[k=10,carry=0.6]", "1-k10-hca100-c0.6"),
        ("B2[hca=0]", "B2[hca=0]", "1-k20-hca0-c0.75"),
    ],
)
def test_parameterised_b2_ids_resolve_to_the_d063_version(
    experiment_id: str, canonical: str, version: str
) -> None:
    """A2: the canonical id lists the given keys as k, hca, carry; EloBaseline names the
    version ("1" with the starting values, 1-k{k}-hca{hca}-c{carry} otherwise)."""
    experiment = resolve_experiment(experiment_id)
    predictor = experiment.make()
    assert experiment.id == canonical
    assert predictor.spec.version == version  # type: ignore[attr-defined]
    assert predictor.spec.model_name == "elo_b2"  # type: ignore[attr-defined]


def test_parameterised_predictor_names_its_constants() -> None:
    """The report header names the constants the run uses, not the class default."""
    predictor = resolve_experiment("B2[k=25,hca=75]").make()
    assert predictor.name == "B2 Elo rating (K=25, HCA=75, carry 0.75; design 5-2)"
    assert (predictor.k, predictor.hca, predictor.carry) == (25.0, 75.0, 0.75)  # type: ignore[attr-defined]
    assert resolve_experiment("B2").make().name.startswith("B2 Elo rating (K=20, HCA=100")


@pytest.mark.parametrize(
    "experiment_id",
    [
        "H-99",
        "B2[mov=1]",
        "B2[x=1]",
        "B2[K=25]",
        "B2[k=]",
        "B2[k=abc]",
        "B2[k=1,k=2]",
        "B2[]",
        "B2[k=25",
        "B2[k=25]x",
        "B0[k=25]",
        "B3[hca=50]",
        "B2[k=0]",
        "B2[hca=-1]",
        "B2[carry=1.5]",
        "B2[k=nan]",
        "B2[k=inf]",
        "B2[k=25;hca=75]",
    ],
)
def test_bad_experiment_ids_are_refused(experiment_id: str) -> None:
    """A2: unknown experiment, unknown key, mov, bad format, repeated key, value refused by
    EloBaseline, parameters on another experiment."""
    with pytest.raises(ExperimentIdError):
        resolve_experiment(experiment_id)


def test_experiment_lists_split_outside_brackets_only() -> None:
    """'B0,B2[k=25,hca=75],B1' is three ids; empty items and open brackets are errors."""
    assert split_experiment_list("B0, B2[k=25,hca=75] ,B1") == ["B0", "B2[k=25,hca=75]", "B1"]
    for bad in ("B0,,B1", "B2[k=25", "B2]k=25[", ",B0"):
        with pytest.raises(ExperimentIdError):
            split_experiment_list(bad)


def main(*args: str, engine: Engine | None = None) -> int:
    """presagio ... on the given engine (None: arguments must fail before any connection)."""
    return cli.main(list(args), engine=engine, now=NOW, setup_logging=False)


@pytest.mark.parametrize(
    "args",
    [
        ("run", "--experiment", "B2[mov=1]", "--seasons", "2023-24"),
        ("run", "--experiment", "B2[k=25,x=1]", "--seasons", "2023-24"),
        ("run", "--experiment", "B2[k=-5]", "--seasons", "2023-24"),
        ("run", "--experiment", "B1[k=25]", "--seasons", "2023-24"),
        ("compare", "--experiments", "B0,B9", "--reference", "B0", "--seasons", "2023-24"),
        ("compare", "--experiments", "B0,B2[mov=1]", "--reference", "B0", "--seasons", "2023-24"),
        ("compare", "--experiments", "B0,B0", "--reference", "B0", "--seasons", "2023-24"),
        (
            "compare",
            "--experiments",
            "B2[k=25,hca=75],B2[hca=75,k=25.0]",
            "--reference",
            "B2[k=25,hca=75]",
            "--seasons",
            "2023-24",
        ),
        ("compare", "--experiments", "B0,B1", "--reference", "B0"),
        ("grid", "--experiment", "B2", "--param", "k=10,20", "--select-season", "2023-24,2024-25"),
        ("grid", "--experiment", "B2", "--param", "mov=1", "--select-season", "2023-24"),
        ("grid", "--experiment", "B2", "--param", "k=10,20,10", "--select-season", "2023-24"),
        ("grid", "--experiment", "B2", "--param", "k=0,20", "--select-season", "2023-24"),
        ("grid", "--experiment", "B2", "--param", "k", "--select-season", "2023-24"),
        ("grid", "--experiment", "B2", "--param", "k=20", "--fixed", "carry=2"),
        ("grid", "--experiment", "B2", "--param", "k=20", "--select-season", "2023"),
        (
            "grid",
            "--experiment",
            "B2",
            "--param",
            "k=20",
            "--select-season",
            "2023-24",
            "--select-from",
            "2024-13-01",
        ),
    ],
)
def test_argument_errors_exit_64(args: tuple[str, ...]) -> None:
    """Malformed ids, unknown keys, mov, duplicates (also one canonical id spelled twice),
    two selection seasons, bad dates: argparse exits 64 before any database work."""
    with pytest.raises(SystemExit) as exc:
        main("backtest", *args)
    assert exc.value.code == 64


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (
            ("compare", "--experiments", "B0", "--reference", "B0", "--seasons", "2023-24"),
            "at least two experiments",
        ),
        (
            ("compare", "--experiments", "B0,B1", "--reference", "B2", "--seasons", "2023-24"),
            "reference B2 is not one of",
        ),
        (
            ("compare", "--experiments", "B0,B1", "--reference", "B0", "--seasons", "2025-26"),
            "holdout season 2025-26 is locked",
        ),
        (
            (
                "compare",
                "--experiments",
                "B0,B1",
                "--reference",
                "B0",
                "--seasons",
                "2024-25,2025-26",
            ),
            "holdout season 2025-26 is locked",
        ),
        (
            ("grid", "--experiment", "B0", "--param", "k=20", "--select-season", "2023-24"),
            "constants of B2 only",
        ),
        (
            (
                "grid",
                "--experiment",
                "B2[k=25]",
                "--param",
                "hca=100",
                "--select-season",
                "2023-24",
            ),
            "constants of B2 only",
        ),
        (
            ("grid", "--experiment", "B2", "--param", "k=20", "--select-season", "2025-26"),
            "holdout season 2025-26 is locked",
        ),
        (
            (
                "grid",
                "--experiment",
                "B2",
                "--param",
                "k=20",
                "--param",
                "k=25",
                "--select-season",
                "2023-24",
            ),
            "given twice",
        ),
        (
            (
                "grid",
                "--experiment",
                "B2",
                "--param",
                "k=20",
                "--fixed",
                "k=25",
                "--select-season",
                "2023-24",
            ),
            "given twice",
        ),
        (
            ("grid", "--experiment", "B2", "--param", "k=10,15", "--select-season", "2023-24"),
            "must contain the starting value 20",
        ),
    ],
)
def test_usage_errors_after_parsing_exit_64(
    args: tuple[str, ...], message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """One experiment, a reference outside the list, the holdout season (locked, no unlock
    option), a grid on another experiment, a constant given twice, a grid without the
    starting value: exit 64 with one stderr line and no database work (engine None)."""
    assert main("backtest", *args) == 64
    captured = capsys.readouterr()
    assert message in captured.err and captured.out == ""


@pytest.fixture
def clean_store(engine: Engine) -> Iterator[None]:
    """--store commits to the league DB: empty the modeling tables again afterwards."""
    yield
    with engine.begin() as conn:
        for table in reversed(MODELING):
            conn.execute(text(f"DELETE FROM {table}"))


def test_run_accepts_a_parameterised_id_and_stores_its_version(
    engine: Engine, capsys: pytest.CaptureFixture[str], clean_store: None
) -> None:
    """A2 end to end: `backtest run --experiment "B2[k=25,hca=75]" --store` names the
    constants in the header and stores model_versions elo_b2 v1-k25-hca75-c0.75 with the
    canonical experiment id."""
    code = main(
        "backtest",
        "run",
        "--experiment",
        "B2[hca=75, k=25]",
        "--seasons",
        "2024-25",
        "--store",
        "--bootstrap",
        "20",
        engine=engine,
    )
    out = capsys.readouterr().out
    assert code == 0
    assert out.startswith("# Backtest B2[k=25,hca=75]: B2 Elo rating (K=25, HCA=75, carry 0.75")
    assert "model_versions elo_b2 v1-k25-hca75-c0.75" in out
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT mv.version, mv.hyperparameters->>'k', mv.hyperparameters->>'hca', "
                "mv.experiment_id, count(*) FROM predictions p "
                "JOIN model_versions mv USING (model_version_id) GROUP BY 1, 2, 3, 4"
            )
        ).all()
    assert rows == [("1-k25-hca75-c0.75", "25.0", "75.0", "B2[k=25,hca=75]", 171)]
