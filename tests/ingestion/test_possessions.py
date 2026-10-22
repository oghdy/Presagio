"""Possession estimate: hand-computed values from fixture game 0022300061 (LAL @ DEN)."""

import pytest

from presagio.ingestion.possessions import (
    FTA_WEIGHT,
    game_possessions,
    possessions_of,
    team_possessions,
)
from presagio.ingestion.sources.base import TeamGameStatRecord

DEN, LAL = 1610612743, 1610612747


def line(team_id: int, opponent_id: int, **counts: int) -> TeamGameStatRecord:
    """A box-score line with zeros except the given counts."""
    values = dict.fromkeys(
        ("pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "reb"), 0
    )
    values.update(dict.fromkeys(("ast", "stl", "blk", "tov", "pf", "plus_minus"), 0))
    values.update(counts)
    return TeamGameStatRecord(
        game_id="0022300061",
        team_id=team_id,
        opponent_team_id=opponent_id,
        is_home=team_id == DEN,
        minutes=240,
        **values,
    )


# leaguegamelog__2023-24_RegularSeason: DEN FGA 91, OREB 9, TOV 12, FTA 12;
# LAL FGA 90, OREB 13, TOV 12, FTA 20.
DEN_LOG = line(DEN, LAL, fga=91, oreb=9, tov=12, fta=12)
LAL_LOG = line(LAL, DEN, fga=90, oreb=13, tov=12, fta=20)


def test_weight_is_oliver_044() -> None:
    """The free-throw weight is 0.44 (Oliver), not basketball-reference's 0.4."""
    assert FTA_WEIGHT == 0.44


def test_team_possessions_by_hand() -> None:
    """DEN: 91 - 9 + 12 + 0.44 * 12 = 99.28; LAL: 90 - 13 + 12 + 0.44 * 20 = 97.8."""
    assert team_possessions(91, 9, 12, 12) == pytest.approx(99.28)
    assert possessions_of(DEN_LOG) == pytest.approx(99.28)
    assert possessions_of(LAL_LOG) == pytest.approx(97.8)


def test_game_possessions_average_both_teams() -> None:
    """(99.28 + 97.8) / 2 = 98.54, the same from either side."""
    assert game_possessions(DEN_LOG, LAL_LOG) == pytest.approx(98.54)
    assert game_possessions(LAL_LOG, DEN_LOG) == pytest.approx(98.54)


def test_tov_definition_moves_the_estimate() -> None:
    """Box score turnovers 11 (players only) instead of 12: 98.28, one possession less."""
    box_den = line(DEN, LAL, fga=91, oreb=9, tov=11, fta=12)
    assert possessions_of(box_den) == pytest.approx(98.28)


def test_no_free_throws() -> None:
    """FTA 0: just FGA - OREB + TOV = 80 - 10 + 15 = 85."""
    assert team_possessions(80, 10, 15, 0) == pytest.approx(85.0)


@pytest.mark.parametrize("missing", ["fga", "oreb", "tov", "fta"])
def test_missing_input_gives_none(missing: str) -> None:
    """Any NULL input -> NULL estimate (team and game level)."""
    values: dict[str, int | None] = {"fga": 91, "oreb": 9, "tov": 12, "fta": 12}
    values[missing] = None
    assert team_possessions(**values) is None

    class Partial:
        """A line with one count unknown."""

        fga, oreb, tov, fta = values["fga"], values["oreb"], values["tov"], values["fta"]

    assert possessions_of(Partial()) is None
    assert game_possessions(Partial(), LAL_LOG) is None
    assert game_possessions(DEN_LOG, Partial()) is None
