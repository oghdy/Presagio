"""What a predictor can reach (review P-032 finding 1): the context holds no score of a game
outside results() — not the target's, not a same-day or later game's — whatever attribute
path a predictor takes, private ones included."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import FrozenInstanceError, fields, is_dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Connection

from presagio.evaluation.data import GameRecord
from presagio.evaluation.walkforward import (
    GameResult,
    Prediction,
    PredictionContext,
    TargetGame,
    TrainingMatrix,
    WalkForwardConfig,
    run_walk_forward,
)

from .league import CUP_FINAL, League

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
ATOMS = (str, bytes, int, float, bool, Decimal, date, datetime, timedelta, type(None))


def reachable(root: Any) -> list[Any]:
    """Every object reachable from root through dataclass fields, instance dicts, mappings
    and collections (private attributes included)."""
    seen: set[int] = set()
    out: list[Any] = []
    stack = [root]
    while stack:
        obj = stack.pop()
        if isinstance(obj, ATOMS) or id(obj) in seen:
            continue
        seen.add(id(obj))
        out.append(obj)
        if is_dataclass(obj) and not isinstance(obj, type):
            stack += [getattr(obj, f.name) for f in fields(obj)]
        if isinstance(obj, Mapping):
            stack += [*obj.keys(), *obj.values()]
        elif isinstance(obj, list | tuple | set | frozenset):
            stack += list(obj)
        if hasattr(obj, "__dict__") and not isinstance(obj, type):
            stack += list(vars(obj).values())
    return out


def score_holders(root: Any) -> list[Any]:
    """Reachable objects that carry a game score."""
    return [o for o in reachable(root) if hasattr(o, "home_score") or hasattr(o, "away_score")]


class Snoop:
    """A predictor that searches everything it was handed for scores, every day."""

    name = "snoop"
    trains = False
    feature_version = None

    def __init__(self) -> None:
        """Collect violations and how many days were inspected."""
        self.violations: list[str] = []
        self.days = 0
        self.schedule_sizes: dict[date, int] = {}

    def fit(self, training: TrainingMatrix) -> None:
        """Not a learner."""

    def predict(
        self, as_of: datetime, games: Sequence[TargetGame], context: PredictionContext
    ) -> Mapping[str, Prediction]:
        """Record any score outside results(); predict 0.5."""
        self.days += 1
        visible = {r.game_id for r in context.results()}
        for holder in score_holders((games, context)):
            if not isinstance(holder, GameResult) or holder.game_id not in visible:
                self.violations.append(f"{context.target_date}: {type(holder).__name__}")
        if any(isinstance(o, GameRecord) for o in reachable((games, context))):
            self.violations.append(f"{context.target_date}: GameRecord reachable")
        for r in context.results():
            if not r.et_date < context.target_date:
                self.violations.append(f"{context.target_date}: result {r.game_id} same day")
        for s in context.schedule():
            if hasattr(s, "home_score") or hasattr(s, "status_code"):
                self.violations.append(f"{context.target_date}: schedule has a result")
            if not s.et_date < context.target_date:
                self.violations.append(f"{context.target_date}: schedule {s.game_id} not past")
        self.schedule_sizes[context.target_date] = len(context.schedule())
        return {g.game_id: Prediction(p_home=0.5) for g in games}


@pytest.mark.parametrize("seasons", [("2023-24", "2024-25"), ("2026-27",)])
def test_no_score_outside_results_is_reachable(conn: Connection, seasons: tuple[str, ...]) -> None:
    """Every day of the run: the only score holders reachable from (games, context) are
    GameResults of results(); no GameRecord (the runner's list) is reachable."""
    snoop = Snoop()
    run_walk_forward(conn, snoop, WalkForwardConfig(seasons=seasons), now=NOW)
    assert snoop.days > 3
    assert snoop.violations == []


def test_schedule_view_includes_the_cup_final_without_scores(
    conn: Connection, league: League
) -> None:
    """schedule() lists earlier dates with the Cup final (M-3 rest features); results() does
    not have the Cup final."""
    seen: dict[str, Any] = {}

    class Keep(Snoop):
        """Keep the context of 2023-12-11 (after the 12-10 Cup final)."""

        def predict(
            self, as_of: datetime, games: Sequence[TargetGame], context: PredictionContext
        ) -> Mapping[str, Prediction]:
            """Store one day's context."""
            if context.target_date == date(2023, 12, 11):
                seen["context"] = context
            return super().predict(as_of, games, context)

    keep = Keep()
    run_walk_forward(conn, keep, WalkForwardConfig(seasons=("2023-24",)), now=NOW)
    context = seen["context"]
    assert CUP_FINAL in {s.game_id for s in context.schedule()}
    assert CUP_FINAL not in {r.game_id for r in context.results()}
    assert keep.violations == []
    expected = sum(
        1
        for g in league.games.values()
        if g["start_time_utc"] < datetime(2023, 12, 11, 5, tzinfo=UTC)
    )
    assert len(context.schedule()) == expected


def test_context_is_frozen(conn: Connection) -> None:
    """A predictor cannot move the information boundary or swap the results."""
    holder: dict[str, PredictionContext] = {}

    class Grab(Snoop):
        """Keep the first context."""

        def predict(
            self, as_of: datetime, games: Sequence[TargetGame], context: PredictionContext
        ) -> Mapping[str, Prediction]:
            """Store the context."""
            holder.setdefault("context", context)
            return super().predict(as_of, games, context)

    run_walk_forward(conn, Grab(), WalkForwardConfig(seasons=("2026-27",)), now=NOW)
    context = holder["context"]
    for name, value in (("as_of", NOW), ("target_date", date(2030, 1, 1)), ("visible_results", ())):
        with pytest.raises(FrozenInstanceError):
            setattr(context, name, value)
    with pytest.raises(TypeError):
        context.markets["x"] = None  # type: ignore[index]
