"""games loader: schedule upserts and result updates under the per-source column rules (6-1).

- Schedule (ScheduleBatch): INSERT ... ON CONFLICT (game_id) DO UPDATE of every column the
  schedule owns, guarded by games.fetched_at <= EXCLUDED.fetched_at (1-4).
- Results (ResultBatch, e.g. LeagueGameLog): UPDATE of status and scores only, on games the
  schedule already created. A result source has no start time, so it cannot insert a game.
  When it disagrees with the schedule (teams, home/away, a different final score) the
  schedule wins and the disagreement is recorded as a quality issue, not raised.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Connection, Engine, bindparam, case, literal, select, update
from sqlalchemy.dialects.postgresql import insert

from presagio.db.models import Game, Team
from presagio.ingestion.loaders.base import (
    INSERTED,
    UpsertCounts,
    chunks,
    count_returned,
    record_issues,
    table_of,
)
from presagio.ingestion.runs import track_run
from presagio.ingestion.sources.base import (
    QualityIssue,
    ResultBatch,
    ResultSource,
    ScheduleBatch,
    ScheduleSource,
)

GAMES = table_of(Game)
TEAMS = table_of(Team)
JOB_SCHEDULE = "schedule"
JOB_GAMELOG = "gamelog"
STATUS_FINAL = 3
# A result source says "final" but has no status text; a schedule reload replaces this.
FINAL_STATUS_TEXT = "Final"
# Columns a schedule source owns (6-1 table, column "scheduleleaguev2").
SCHEDULE_COLUMNS = (
    "season",
    "game_subtype",
    "start_time_utc",
    "is_time_tbd",
    "status_code",
    "status_text",
    "postponed_status",
    "home_team_id",
    "away_team_id",
    "home_score",
    "away_score",
    "is_neutral",
    "if_necessary",
    "source",
    "fetched_at",
    "raw_response_id",
)


@dataclass(frozen=True)
class ScheduleLoadResult:
    """Outcome of loading one ScheduleBatch; failed holds every listed game not stored."""

    counts: UpsertCounts
    skipped: dict[str, int]
    failed: dict[str, str] = field(default_factory=dict)
    issues: tuple[QualityIssue, ...] = ()


@dataclass(frozen=True)
class ResultLoadResult:
    """Outcome of applying one ResultBatch.

    updated results were written; stale ones lost to a newer games row; conflicting ones
    disagreed with the schedule (issues say how) and were dropped; missing ones have no
    games row yet.
    """

    updated: int
    stale: int
    conflicting: int
    missing: tuple[str, ...]
    issues: tuple[QualityIssue, ...] = ()


def known_team_ids(conn: Connection) -> set[int]:
    """Team IDs present in teams (games.home/away_team_id reference them)."""
    return set(conn.execute(select(TEAMS.c.team_id)).scalars())


def upsert_schedule(conn: Connection, batch: ScheduleBatch) -> ScheduleLoadResult:
    """Upsert the batch's games; games naming a team missing from teams are left out."""
    provenance = batch.provenance
    if provenance.raw_response_id is None:
        raise ValueError("games rows need the raw response they came from (raw_response_id)")
    teams = known_team_ids(conn)
    rows: list[dict[str, Any]] = []
    # Games the adapter could not use count as failed too, so the run cannot end succeeded.
    failed: dict[str, str] = dict(batch.rejected)
    for game in batch.games:
        unknown = [
            team_id
            for team_id in (game.home_team_id, game.away_team_id)
            if team_id is not None and team_id not in teams
        ]
        if unknown:
            failed[game.game_id] = f"team id(s) {unknown} not in teams; load teams first"
            continue
        rows.append(
            {
                "game_id": game.game_id,
                "season": game.season,
                "game_subtype": game.game_subtype,
                "start_time_utc": game.start_time_utc,
                "is_time_tbd": game.is_time_tbd,
                "status_code": game.status_code,
                "status_text": game.status_text,
                "postponed_status": game.postponed_status,
                "home_team_id": game.home_team_id,
                "away_team_id": game.away_team_id,
                "home_score": game.home_score,
                "away_score": game.away_score,
                "is_neutral": game.is_neutral,
                "if_necessary": game.if_necessary,
                "source": provenance.source,
                "fetched_at": provenance.fetched_at,
                "raw_response_id": provenance.raw_response_id,
            }
        )
    counts = UpsertCounts()
    for chunk in chunks(rows):
        stmt = insert(GAMES).values(list(chunk))
        stmt = stmt.on_conflict_do_update(
            index_elements=[GAMES.c.game_id],
            set_={name: stmt.excluded[name] for name in SCHEDULE_COLUMNS},
            where=GAMES.c.fetched_at <= stmt.excluded.fetched_at,
        ).returning(GAMES.c.game_id, INSERTED)
        counts += count_returned(conn.execute(stmt).all(), len(chunk))
    return ScheduleLoadResult(counts, dict(batch.skipped), failed, batch.issues)


def apply_results(conn: Connection, batch: ResultBatch) -> ResultLoadResult:
    """Set status final and scores on existing games, trusting the schedule on conflicts."""
    provenance = batch.provenance
    if provenance.raw_response_id is None:
        raise ValueError("games rows need the raw response they came from (raw_response_id)")
    ids = [result.game_id for result in batch.results]
    existing = {
        row.game_id: row
        for row in conn.execute(
            select(
                GAMES.c.game_id,
                GAMES.c.home_team_id,
                GAMES.c.away_team_id,
                GAMES.c.status_code,
                GAMES.c.home_score,
                GAMES.c.away_score,
                GAMES.c.fetched_at,
            )
            .where(GAMES.c.game_id.in_(ids))
            .with_for_update()
        )
    }
    issues = list(batch.issues)
    missing: list[str] = []
    stale = 0
    conflicting = 0
    updates: list[dict[str, Any]] = []
    for result in batch.results:
        game = existing.get(result.game_id)
        if game is None:
            missing.append(result.game_id)
            continue
        if game.fetched_at > provenance.fetched_at:
            stale += 1
            continue
        reported = {result.home_team_id, result.away_team_id}
        if game.home_team_id is None or reported != {game.home_team_id, game.away_team_id}:
            issues.append(
                QualityIssue(
                    "team_mismatch",
                    result.game_id,
                    f"schedule {game.home_team_id} vs {game.away_team_id}, "
                    f"{provenance.source} {result.home_team_id} vs {result.away_team_id}",
                )
            )
            conflicting += 1
            continue
        # Scores follow team IDs, so a flipped home/away cannot put a score on the wrong team.
        points = {result.home_team_id: result.home_score, result.away_team_id: result.away_score}
        home_score, away_score = points[game.home_team_id], points[game.away_team_id]
        if result.home_team_id != game.home_team_id:
            issues.append(
                QualityIssue(
                    "home_away_mismatch",
                    result.game_id,
                    f"schedule home {game.home_team_id}, {provenance.source} home "
                    f"{result.home_team_id}; schedule kept",
                )
            )
        if game.status_code == STATUS_FINAL and (game.home_score, game.away_score) != (
            home_score,
            away_score,
        ):
            issues.append(
                QualityIssue(
                    "score_mismatch",
                    result.game_id,
                    f"schedule {game.home_score}-{game.away_score}, "
                    f"{provenance.source} {home_score}-{away_score}; schedule kept",
                )
            )
            conflicting += 1
            continue
        updates.append(
            {"b_game_id": result.game_id, "b_home_score": home_score, "b_away_score": away_score}
        )
    if updates:
        stmt = (
            update(GAMES)
            .where(GAMES.c.game_id == bindparam("b_game_id"))
            .where(GAMES.c.fetched_at <= provenance.fetched_at)
            .values(
                status_code=STATUS_FINAL,
                # Keep the schedule's own final text ("Final/OT2"); only a newly final game
                # gets the generic one.
                status_text=case(
                    (GAMES.c.status_code == STATUS_FINAL, GAMES.c.status_text),
                    else_=literal(FINAL_STATUS_TEXT),
                ),
                home_score=bindparam("b_home_score"),
                away_score=bindparam("b_away_score"),
                source=provenance.source,
                fetched_at=provenance.fetched_at,
                raw_response_id=provenance.raw_response_id,
            )
        )
        conn.execute(stmt, updates)
    return ResultLoadResult(len(updates), stale, conflicting, tuple(missing), tuple(issues))


def load_season_schedule(
    source: ScheduleSource,
    engine: Engine,
    season: str,
    *,
    stale_after_hours: float | None = None,
) -> ScheduleLoadResult:
    """Fetch one season's schedule and upsert it as one ingestion run (job_type 'schedule')."""
    with track_run(
        engine,
        JOB_SCHEDULE,
        f"season={season}",
        source=source.name,
        season=season,
        stale_after_hours=stale_after_hours,
    ) as run:
        batch = source.fetch_season_schedule(season, run)
        # Every game of a stored type the source listed, whether we could use it or not.
        run.items_total = len(batch.games) + len(batch.rejected)
        with engine.begin() as conn:
            result = upsert_schedule(conn, batch)
        for _ in range(run.items_total - len(result.failed)):
            run.item_succeeded()
        for game_id, reason in result.failed.items():
            run.item_failed(game_id, reason)
        run.rows_written = result.counts.written
        run.detail.update(
            games=result.counts.as_dict(),
            skipped_game_types=result.skipped,
            raw_response_id=batch.provenance.raw_response_id,
        )
        record_issues(run, result.issues)
    return result


def load_season_results(
    source: ResultSource,
    engine: Engine,
    season: str,
    season_type: str,
    *,
    stale_after_hours: float | None = None,
) -> ResultLoadResult:
    """Fetch one season type's final scores and apply them as one run (job_type 'gamelog')."""
    label = source.season_type_labels[season_type]
    with track_run(
        engine,
        JOB_GAMELOG,
        f"season={season};season_type={label}",
        source=source.name,
        season=season,
        season_type=label,
        stale_after_hours=stale_after_hours,
    ) as run:
        batch = source.fetch_season_results(season, season_type, run)
        run.items_total = len(batch.results)
        with engine.begin() as conn:
            result = apply_results(conn, batch)
        # Only applied results succeed; stale and conflicting ones are counted in the detail.
        for _ in range(result.updated):
            run.item_succeeded()
        # Design 1-4: a result for a game the schedule has not created yet is reported as a
        # failed item and retried by the next run (after the next schedule load).
        for game_id in result.missing:
            run.item_failed(game_id, "game not in games yet; load the schedule first")
        run.rows_written = result.updated
        run.detail.update(
            updated=result.updated,
            skipped={"stale": result.stale, "conflicting": result.conflicting},
            raw_response_id=batch.provenance.raw_response_id,
        )
        record_issues(run, result.issues)
    return result
