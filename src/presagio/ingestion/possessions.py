"""Possession estimates from team box-score counts (pure functions, nothing is stored).

Formula (team level, one game):

    POSS ~= FGA - OREB + TOV + 0.44 * FTA

A possession ends with a made or missed shot that the defense rebounds, a turnover, or free
throws that end the trip. FGA - OREB counts shot-ending possessions (an offensive rebound
continues the same possession), TOV adds turnovers, and 0.44 * FTA converts free-throw
attempts into the possessions they end: two-shot fouls end one possession per two attempts,
while and-ones, three-shot fouls and technicals end fewer, hence a weight below 0.5.

Sources: the approximation and the 0.44 weight are credited to Dean Oliver, "Basketball on
Paper" (2004); basketball-reference uses the same 0.44 in its Four Factors TOV% denominator.
Alternatives not used here: basketball-reference's "Poss" (0.4 * FTA plus a 1.07 offensive
rebound share term needing the opponent's DREB), and 0.475 * FTA (college analytics).

The game estimate averages both teams. Both sides have almost the same number of
possessions, so the average cancels part of each team's estimation error (team rebounds and
team turnovers are not in every source's TOV, schema 6-2 A-6). Any missing input -> None.
Schema 6-2 keeps the formula out of the database so ML can version it as a feature (stage 2).
"""

from __future__ import annotations

from typing import Protocol

# Share of free-throw attempts that end a possession (Oliver).
FTA_WEIGHT = 0.44


class PossessionInputs(Protocol):
    """The four counts the estimate needs; TeamGameStatRecord satisfies this."""

    @property
    def fga(self) -> int | None:
        """Field goal attempts."""
        ...

    @property
    def oreb(self) -> int | None:
        """Offensive rebounds."""
        ...

    @property
    def tov(self) -> int | None:
        """Turnovers (definition depends on the source, schema 6-2 A-6)."""
        ...

    @property
    def fta(self) -> int | None:
        """Free throw attempts."""
        ...


def team_possessions(
    fga: int | None, oreb: int | None, tov: int | None, fta: int | None
) -> float | None:
    """One team's possessions: FGA - OREB + TOV + 0.44 * FTA, or None if an input is None."""
    if fga is None or oreb is None or tov is None or fta is None:
        return None
    return fga - oreb + tov + FTA_WEIGHT * fta


def possessions_of(line: PossessionInputs) -> float | None:
    """team_possessions for a box-score line."""
    return team_possessions(line.fga, line.oreb, line.tov, line.fta)


def game_possessions(team: PossessionInputs, opponent: PossessionInputs) -> float | None:
    """Possessions per team in one game: the mean of both teams' estimates, or None."""
    ours, theirs = possessions_of(team), possessions_of(opponent)
    if ours is None or theirs is None:
        return None
    return (ours + theirs) / 2
