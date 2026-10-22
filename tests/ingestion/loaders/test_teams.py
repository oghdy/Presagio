"""teams / team_aliases loader on a temp database (D-030)."""

from sqlalchemy import Engine, text

from presagio.ingestion.loaders import load_teams

from ..helpers import T0
from .conftest import NbaApiStub


def counts(db: Engine) -> tuple[int, int]:
    """(teams rows, team_aliases rows)."""
    with db.connect() as conn:
        return (
            conn.execute(text("SELECT count(*) FROM teams")).scalar_one(),
            conn.execute(text("SELECT count(*) FROM team_aliases")).scalar_one(),
        )


def fetched_ats(db: Engine) -> set[object]:
    """Distinct fetched_at values across teams and aliases."""
    with db.connect() as conn:
        return set(
            conn.execute(
                text("SELECT fetched_at FROM teams UNION SELECT fetched_at FROM team_aliases")
            ).scalars()
        )


def test_load_teams_twice_keeps_row_counts_and_moves_fetched_at(
    db: Engine, nba: NbaApiStub
) -> None:
    """30 teams and 60 aliases after one or two loads; only fetched_at changes."""
    first = load_teams(nba.teams, db)
    assert (first.teams.inserted, first.aliases.inserted) == (30, 60)
    assert counts(db) == (30, 60)
    assert fetched_ats(db) == {T0}

    nba.clock.advance(3600)
    second = load_teams(nba.teams, db)
    assert (second.teams.inserted, second.teams.updated) == (0, 30)
    assert (second.aliases.inserted, second.aliases.updated) == (0, 60)
    assert counts(db) == (30, 60)
    assert fetched_ats(db) == {nba.clock.now()}

    with db.connect() as conn:
        row = conn.execute(
            text("SELECT tricode, city, nickname, full_name, source FROM teams WHERE team_id = :t"),
            {"t": 1610612743},
        ).one()
        runs = conn.execute(
            text("SELECT status, api_calls, rows_written FROM ingestion_runs ORDER BY run_id")
        ).all()
    assert tuple(row) == ("DEN", "Denver", "Nuggets", "Denver Nuggets", "nba_api.static")
    assert [tuple(r) for r in runs] == [("succeeded", 1, 90), ("succeeded", 1, 90)]


def test_team_list_disagreement_ends_partial(db: Engine, nba: NbaApiStub) -> None:
    """A library list that misses an active franchise is loaded but the run says partial."""
    full = nba.teams.static()
    nba.teams.static = lambda: full[:-1]
    result = load_teams(nba.teams, db)
    assert [i.kind for i in result.issues] == ["team_not_in_static_list"]
    with db.connect() as conn:
        status, detail = conn.execute(text("SELECT status, error_detail FROM ingestion_runs")).one()
    assert status == "partial"
    assert detail["quality_issues"][0]["kind"] == "team_not_in_static_list"
    assert counts(db) == (29, 58)
