"""teams / team_aliases loader: TeamBatch -> INSERT ... ON CONFLICT upserts (schema 5)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Connection, Engine
from sqlalchemy.dialects.postgresql import insert

from presagio.db.models import Team, TeamAlias
from presagio.ingestion.loaders.base import (
    INSERTED,
    UpsertCounts,
    count_returned,
    record_issues,
    table_of,
)
from presagio.ingestion.runs import track_run
from presagio.ingestion.sources.base import QualityIssue, TeamBatch, TeamSource

TEAMS = table_of(Team)
ALIASES = table_of(TeamAlias)
JOB_TEAMS = "teams"


@dataclass(frozen=True)
class TeamLoadResult:
    """Outcome of loading one TeamBatch."""

    teams: UpsertCounts
    aliases: UpsertCounts
    issues: tuple[QualityIssue, ...]


def upsert_teams(conn: Connection, batch: TeamBatch) -> TeamLoadResult:
    """Upsert teams, then aliases; an older batch never overwrites a newer one (1-4)."""
    fetched_at = batch.provenance.fetched_at
    team_rows = [
        {
            "team_id": t.team_id,
            "tricode": t.tricode,
            "city": t.city,
            "nickname": t.nickname,
            "full_name": t.full_name,
            "source": batch.provenance.source,
            "fetched_at": fetched_at,
        }
        for t in batch.teams
    ]
    if not team_rows:
        raise ValueError("an empty team list would leave every game without its teams")
    stmt = insert(TEAMS).values(team_rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[TEAMS.c.team_id],
        set_={
            name: stmt.excluded[name]
            for name in ("tricode", "city", "nickname", "full_name", "source", "fetched_at")
        },
        where=TEAMS.c.fetched_at <= stmt.excluded.fetched_at,
    ).returning(TEAMS.c.team_id, INSERTED)
    teams = count_returned(conn.execute(stmt).all(), len(team_rows))

    alias_rows = [
        {"source": a.source, "alias": a.alias, "team_id": a.team_id, "fetched_at": fetched_at}
        for a in batch.aliases
    ]
    aliases = UpsertCounts()
    if alias_rows:
        stmt = insert(ALIASES).values(alias_rows)
        # note is a human annotation; the loader never touches it.
        stmt = stmt.on_conflict_do_update(
            index_elements=[ALIASES.c.source, ALIASES.c.alias],
            set_={"team_id": stmt.excluded.team_id, "fetched_at": stmt.excluded.fetched_at},
            where=ALIASES.c.fetched_at <= stmt.excluded.fetched_at,
        ).returning(ALIASES.c.alias, INSERTED)
        aliases = count_returned(conn.execute(stmt).all(), len(alias_rows))
    return TeamLoadResult(teams, aliases, batch.issues)


def load_teams(
    source: TeamSource, engine: Engine, *, stale_after_hours: float | None = None
) -> TeamLoadResult:
    """Fetch and load all teams as one ingestion run (job_type 'teams')."""
    with track_run(
        engine, JOB_TEAMS, source.name, source=source.name, stale_after_hours=stale_after_hours
    ) as run:
        batch = source.fetch_teams(run)
        run.items_total = len(batch.teams)
        with engine.begin() as conn:
            result = upsert_teams(conn, batch)
        for _ in batch.teams:
            run.item_succeeded()
        run.rows_written = result.teams.written + result.aliases.written
        run.detail.update(
            teams=result.teams.as_dict(),
            aliases=result.aliases.as_dict(),
            checked_raw_response_id=batch.provenance.raw_response_id,
        )
        record_issues(run, batch.issues)
        if batch.issues:
            # A franchise list that disagrees with the API leaves games without a team row.
            run.mark_partial(f"{len(batch.issues)} team list disagreement(s) with the source")
    return result
