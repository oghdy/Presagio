"""Markdown report: two lines per segment, n, the small-sample note (design 4-6)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Connection

from presagio.evaluation.dummy import ConstantPredictor
from presagio.evaluation.report import ReportSettings, render_report
from presagio.evaluation.scoring import score_run
from presagio.evaluation.walkforward import WalkForwardConfig, run_walk_forward
from presagio.models.baselines import MarketBaseline

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
FAST = ReportSettings(n_boot=50)


def report(conn: Connection, seasons: tuple[str, ...], **config: object) -> str:
    """Run, score and render."""
    run = run_walk_forward(
        conn, ConstantPredictor(), WalkForwardConfig(seasons=seasons, **config), now=NOW
    )
    return render_report(run, score_run(conn, run), experiment="H-00", settings=FAST)


def test_dev_seasons_report_both_lines_and_no_market_reason(conn: Connection) -> None:
    """2023-24 and 2024-25: (a) all games and (b) with market (n 0, no_odds_season) for each
    season x phase, plus an all-seasons segment. 2023-24 reports 50 dates x 2 + the 01-11
    game = 101 regular games from 2024-01-01 and 3 postseason games; 70 are warm-up."""
    text = report(conn, ("2023-24", "2024-25"))
    assert "# Backtest H-00" in text and "Holdout locked (2025-26 not evaluated)" in text
    assert "| 2023-24 | regular | (a) all games | 101 |" in text
    assert "| 2023-24 | regular | (b) with market | 0 |" in text
    assert "no market: no_odds_season 101" in text
    assert "| 2024-25 | postseason | (a) all games | 1 |" in text
    assert "| all | all | (a) all games | 275 |" in text
    assert "warm-up (not reported) 70" in text
    assert "nothing was written to the database" in text
    assert "ECE " in text


def test_live_season_marks_small_market_samples(conn: Connection) -> None:
    """2026-27: (b) has n = 2 (available + pred_only) and is marked reference only; the B4
    table holds the single available game with its CLV."""
    text = report(conn, ("2026-27",))
    assert "| 2026-27 | regular | (b) with market | 2 |" in text
    assert "reference only (n < 100)" in text
    b4 = text.split("## vs closing market B4")[1].split("##")[0]
    assert "| 2026-27 | regular | 1 |" in b4
    assert "+0.0769" in b4  # mean clv_return of the one value bet
    counts = text.split("## market_status counts")[1]
    assert "| 2026-27 | regular | 3 | 1 | 1 | 0 | 0 | 1 | 0 |" in counts


def test_market_baseline_without_odds_reports_the_reason(conn: Connection) -> None:
    """B3 over 2023-24 predicts nothing: the header counts the games by their prediction-time
    market_status and no table follows."""
    run = run_walk_forward(conn, MarketBaseline(), WalkForwardConfig(seasons=("2023-24",)), now=NOW)
    text = render_report(run, score_run(conn, run), experiment="B3", settings=FAST)
    assert (
        "not predicted: not_predicted 174 (not predicted by market_status: no_odds_season 174)."
        in text
    )
    assert "No scored predictions." in text


def test_unlocked_holdout_is_announced(conn: Connection) -> None:
    """An unlocked run says so in the header with the reason."""
    text = report(conn, ("2025-26",), unlock_holdout="H-08 final model")
    assert "Holdout UNLOCKED for 2025-26: H-08 final model" in text
