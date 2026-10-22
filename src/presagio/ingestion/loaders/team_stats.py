"""team_game_stats loader: idempotent upserts of team box-score lines (schema 6-2).

- Key (game_id, team_id, source): each source keeps its own rows and values (A-6); the
  view team_game_stats_primary picks leaguegamelog over boxscoretraditionalv3.
- INSERT ... ON CONFLICT DO UPDATE guarded by team_game_stats.fetched_at <= EXCLUDED (1-4).
- FK order (1-4, D-034): the game must already be in games and both teams in teams;
  otherwise the game is a failed item and the run ends partial. A game is stored with both
  lines or not at all.
- The schedule decides home and away (D-034): is_home follows games, a disagreement is a
  quality issue. Points that do not add up to the games final score are a quality issue
  too, never an exception (the rows are still stored, as the source gave them).

load_season_game_log handles one LeagueGameLog response for both tables: the final scores
go through the games loader (apply_results) and the team lines through upsert_team_stats,
in one transaction, so a season type costs one call.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Connection, Engine, select
from sqlalchemy.dialects.postgresql import insert

from presagio.db.models import Game, TeamGameStat
from presagio.ingestion.loaders.base import (
    INSERTED,
    UpsertCounts,
    chunks,
    count_returned,
    record_issues,
    table_of,
)
from presagio.ingestion.loaders.games import (
    JOB_GAMELOG,
    STATUS_FINAL,
    ResultLoadResult,
    apply_results,
    known_team_ids,
)
from presagio.ingestion.runs import RunTracker, track_run
from presagio.ingestion.sources.base import (
    GameTeamStatsSource,
    QualityIssue,
    TeamGameStatRecord,
    TeamStatsBatch,
    TeamStatsSource,
    season_of,
)

GAMES = table_of(Game)
TEAM_GAME_STATS = table_of(TeamGameStat)
JOB_BOXSCORE = "boxscore"
MISSING_GAME = "game not in games yet; load the schedule first"
# Columns a stats source writes on conflict (all of them except the key and created_at).
UPDATE_COLUMNS = tuple(
    f.name for f in dataclasses.fields(TeamGameStatRecord) if f.name not in ("game_id", "team_id")
) + ("fetched_at", "raw_response_id")


@dataclass(frozen=True)
class TeamStatsLoadResult:
    """Outcome of one TeamStatsBatch.

    stored games had both lines written; stale games lost to newer rows (fetched_at guard);
    failed maps every listed game we did not store to the reason.
    """

    counts: UpsertCounts
    stored: tuple[str, ...]
    stale: tuple[str, ...]
    failed: dict[str, str] = field(default_factory=dict)
    issues: tuple[QualityIssue, ...] = ()


@dataclass(frozen=True)
class GameLogLoadResult:
    """Outcome of one LeagueGameLog response: games results and team_game_stats lines."""

    results: ResultLoadResult | None
    stats: TeamStatsLoadResult
    failed: dict[str, str]


def lines_by_game(stats: Iterable[TeamGameStatRecord]) -> dict[str, list[TeamGameStatRecord]]:
    """Team lines grouped by game, in source order."""
    grouped: dict[str, list[TeamGameStatRecord]] = {}
    for line in stats:
        grouped.setdefault(line.game_id, []).append(line)
    return grouped


def check_game(
    game_id: str, lines: list[TeamGameStatRecord], game: Any, teams: set[int], source: str
) -> tuple[list[TeamGameStatRecord] | None, str | None, list[QualityIssue]]:
    """Lines ready to store (home/away from games), or the reason to fail the game; issues."""
    if game is None:
        return None, MISSING_GAME, []
    team_ids = [line.team_id for line in lines]
    if len(lines) != 2 or len(set(team_ids)) != 2:
        return None, f"expected two different teams, got {team_ids}", []
    unknown = [team_id for team_id in team_ids if team_id not in teams]
    if unknown:
        return None, f"team id(s) {unknown} not in teams; load teams first", []
    if set(team_ids) != {game.home_team_id, game.away_team_id}:
        reason = (
            f"team_mismatch: games {game.home_team_id} vs {game.away_team_id}, "
            f"{source} {team_ids[0]} vs {team_ids[1]}"
        )
        return None, reason, [QualityIssue("team_mismatch", game_id, reason)]
    issues: list[QualityIssue] = []
    source_home = [line.team_id for line in lines if line.is_home]
    if source_home != [game.home_team_id]:
        issues.append(
            QualityIssue(
                "home_away_mismatch",
                game_id,
                f"games home {game.home_team_id}, {source} home {source_home}; games kept",
            )
        )
    lines = [dataclasses.replace(line, is_home=line.team_id == game.home_team_id) for line in lines]
    points = {line.team_id: line.pts for line in lines}
    if game.status_code != STATUS_FINAL or game.home_score is None:
        issues.append(
            QualityIssue("points_unchecked", game_id, "game is not final in games; not compared")
        )
    elif (points[game.home_team_id], points[game.away_team_id]) != (
        game.home_score,
        game.away_score,
    ):
        issues.append(
            QualityIssue(
                "points_mismatch",
                game_id,
                f"games {game.home_score}-{game.away_score} "
                f"(sum {game.home_score + game.away_score}), {source} "
                f"{points[game.home_team_id]}-{points[game.away_team_id]} "
                f"(sum {sum(points.values())}); rows stored as given",
            )
        )
    return lines, None, issues


def upsert_team_stats(conn: Connection, batch: TeamStatsBatch) -> TeamStatsLoadResult:
    """Upsert the batch's team lines; games that fail a check are left out and reported."""
    provenance = batch.provenance
    if provenance.raw_response_id is None:
        raise ValueError("team_game_stats rows need the raw response they came from")
    grouped = lines_by_game(batch.stats)
    games = {
        row.game_id: row
        for row in conn.execute(
            select(
                GAMES.c.game_id,
                GAMES.c.home_team_id,
                GAMES.c.away_team_id,
                GAMES.c.status_code,
                GAMES.c.home_score,
                GAMES.c.away_score,
            ).where(GAMES.c.game_id.in_(list(grouped)))
        )
    }
    teams = known_team_ids(conn)
    failed: dict[str, str] = dict(batch.rejected)
    issues = list(batch.issues)
    rows: list[dict[str, Any]] = []
    for game_id, lines in grouped.items():
        checked, reason, game_issues = check_game(
            game_id, lines, games.get(game_id), teams, provenance.source
        )
        issues.extend(game_issues)
        if checked is None:
            failed[game_id] = reason or "not stored"
            continue
        for line in checked:
            rows.append(
                {
                    **dataclasses.asdict(line),
                    "source": provenance.source,
                    "fetched_at": provenance.fetched_at,
                    "raw_response_id": provenance.raw_response_id,
                }
            )
    counts = UpsertCounts()
    written: set[str] = set()
    for chunk in chunks(rows):
        stmt = insert(TEAM_GAME_STATS).values(list(chunk))
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                TEAM_GAME_STATS.c.game_id,
                TEAM_GAME_STATS.c.team_id,
                TEAM_GAME_STATS.c.source,
            ],
            set_={name: stmt.excluded[name] for name in UPDATE_COLUMNS},
            where=TEAM_GAME_STATS.c.fetched_at <= stmt.excluded.fetched_at,
        ).returning(TEAM_GAME_STATS.c.game_id, INSERTED)
        returned = conn.execute(stmt).all()
        written.update(row.game_id for row in returned)
        counts += count_returned(returned, len(chunk))
    attempted = {row["game_id"] for row in rows}
    return TeamStatsLoadResult(
        counts,
        tuple(sorted(written)),
        tuple(sorted(attempted - written)),
        failed,
        tuple(issues),
    )


def close_items(run: RunTracker, stored: int, failed: dict[str, str]) -> None:
    """Count stored games as succeeded and every failed game as a failed item."""
    for _ in range(stored):
        run.item_succeeded()
    for game_id, reason in failed.items():
        run.item_failed(game_id, reason)


def load_season_game_log(
    source: TeamStatsSource,
    engine: Engine,
    season: str,
    season_type: str,
    *,
    stale_after_hours: float | None = None,
) -> GameLogLoadResult:
    """Fetch one season type's game log once; apply its scores to games and store team lines.

    One run (job_type 'gamelog', same scope as load_season_results): items are the games the
    response lists; a game succeeds when its two team lines are written.
    """
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
        batch = source.fetch_season_team_stats(season, season_type, run)
        listed = {line.game_id for line in batch.stats} | set(batch.rejected)
        run.items_total = len(listed)
        with engine.begin() as conn:
            # Scores first, so the points check below compares with this response's finals.
            results = apply_results(conn, batch.results) if batch.results is not None else None
            stats = upsert_team_stats(conn, batch)
        failed = dict(stats.failed)
        for game_id in results.missing if results is not None else ():
            failed.setdefault(game_id, MISSING_GAME)
        close_items(run, len(stats.stored), failed)
        run.rows_written = stats.counts.written + (results.updated if results else 0)
        if results is not None:
            run.detail["games"] = {
                "updated": results.updated,
                "skipped": {"stale": results.stale, "conflicting": results.conflicting},
            }
            record_issues(run, results.issues)
        run.detail.update(
            team_game_stats=stats.counts.as_dict(),
            stale_games=len(stats.stale),
            raw_response_id=batch.provenance.raw_response_id,
        )
        record_issues(run, stats.issues)
    return GameLogLoadResult(results, stats, failed)


def load_game_box_score(
    source: GameTeamStatsSource,
    engine: Engine,
    game_id: str,
    *,
    stale_after_hours: float | None = None,
) -> TeamStatsLoadResult:
    """Fetch one game's box score and store its team lines (job_type 'boxscore', opt-in)."""
    with track_run(
        engine,
        JOB_BOXSCORE,
        f"game={game_id}",
        source=source.name,
        season=season_of(game_id),
        stale_after_hours=stale_after_hours,
        items_total=1,
    ) as run:
        batch = source.fetch_game_team_stats(game_id, run)
        with engine.begin() as conn:
            stats = upsert_team_stats(conn, batch)
        close_items(run, len(stats.stored), stats.failed)
        run.rows_written = stats.counts.written
        run.detail.update(
            team_game_stats=stats.counts.as_dict(),
            stale_games=len(stats.stale),
            raw_response_id=batch.provenance.raw_response_id,
        )
        record_issues(run, stats.issues)
    return stats
