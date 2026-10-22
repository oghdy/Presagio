"""odds_events / odds_snapshots loader and event -> game mapping (schema v1 ch. 7, D-028).

- Events: INSERT ... ON CONFLICT (source, source_event_id) DO UPDATE of names and
  commence_time, guarded by fetched_at (1-4). One row per provider event however often seen.
- Snapshots: INSERT ... ON CONFLICT (odds_event_id, market_key, bookmaker_key, outcome_side,
  captured_at) DO NOTHING. Every capture adds rows at its own captured_at; the same capture
  loaded twice adds nothing. Prices captured at or after commence_time are never written.
- A live capture's captured_at/fetched_at is the fetched_at of its raw row (1-3, 7-2): a
  response identical to an earlier one lands on the same raw row, so it adds no snapshots.
- An event id listed twice in one response keeps its first listing; the later ones are left
  out with a 'duplicate_event' quality issue and the rest of the batch loads (P-017).
- Mapping (A-9), only here: both team names known in team_aliases and exactly one game with
  those home/away teams starting within +-6 h of commence_time. Otherwise game_id stays NULL
  and a quality issue says why; remap_unmatched_events retries once games are loaded.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Connection, Engine, and_, func, select, update
from sqlalchemy.dialects.postgresql import insert

from presagio.db.models import Game, OddsEvent, OddsSnapshot, RawApiResponse, Team, TeamAlias
from presagio.ingestion.loaders.base import (
    INSERTED,
    UpsertCounts,
    chunks,
    count_returned,
    record_issues,
    table_of,
)
from presagio.ingestion.runs import RunTracker, track_run
from presagio.ingestion.sources.base import QualityIssue
from presagio.ingestion.sources.odds.base import (
    SKIPPED_REJECTED_OUTCOMES,
    SKIPPED_STARTED_OUTCOMES,
    OddsBatch,
    OddsEventRecord,
    OddsSource,
)
from presagio.ingestion.sources.odds.the_odds_api import SOURCE as THE_ODDS_API
from presagio.ingestion.sources.odds.the_odds_api import TEAM_NAME_SEED, TEAM_NAME_SEED_NOTE

EVENTS = table_of(OddsEvent)
SNAPSHOTS = table_of(OddsSnapshot)
ALIASES = table_of(TeamAlias)
GAMES = table_of(Game)
TEAMS = table_of(Team)
RAW = table_of(RawApiResponse)
JOB_ODDS = "odds"
JOB_ODDS_EVENTS = "odds_events"
# A-9: our start time and the provider's commence_time may differ by at most this much.
MATCH_WINDOW = timedelta(hours=6)
MATCH_METHOD = "alias+time"


@dataclass(frozen=True)
class MappingResult:
    """Outcome of one mapping pass over unmatched events.

    max_time_diff_seconds is the largest |games.start_time_utc - commence_time| among the
    events matched in this pass (schema 7-1 asks to measure it), None when nothing matched.
    """

    checked: int = 0
    matched: int = 0
    issues: tuple[QualityIssue, ...] = ()
    max_time_diff_seconds: float | None = None

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready summary for ingestion_runs.error_detail."""
        return {
            "checked": self.checked,
            "matched": self.matched,
            "unmatched": self.checked - self.matched,
            "max_time_diff_seconds": self.max_time_diff_seconds,
        }


@dataclass(frozen=True)
class OddsLoadResult:
    """Outcome of storing one OddsBatch.

    captured_at is the capture time actually used (the raw row's first receipt for live);
    snapshots_duplicate are prices already stored for the same capture (a reload);
    skipped counts prices left out on purpose (started events, unusable prices);
    failed holds the events the provider listed that the adapter could not read.
    """

    captured_at: datetime
    events: UpsertCounts
    snapshots_inserted: int
    snapshots_duplicate: int
    mapping: MappingResult
    skipped: dict[str, int] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)
    issues: tuple[QualityIssue, ...] = ()


def upsert_odds_events(conn: Connection, batch: OddsBatch) -> tuple[dict[str, int], UpsertCounts]:
    """Upsert the batch's events; return {source_event_id: odds_event_id} and the counts."""
    provenance = batch.provenance
    if provenance.raw_response_id is None:
        raise ValueError("odds rows need the raw response they came from (raw_response_id)")
    rows = [
        {
            "source": batch.source,
            "source_event_id": event.source_event_id,
            "home_team_name": event.home_team_name,
            "away_team_name": event.away_team_name,
            "commence_time": event.commence_time,
            "fetched_at": provenance.fetched_at,
            "raw_response_id": provenance.raw_response_id,
        }
        for event in batch.events
    ]
    counts = UpsertCounts()
    for chunk in chunks(rows):
        stmt = insert(EVENTS).values(list(chunk))
        stmt = stmt.on_conflict_do_update(
            constraint="uq_odds_events_source_id",
            set_={
                name: stmt.excluded[name]
                for name in (
                    "home_team_name",
                    "away_team_name",
                    "commence_time",
                    "fetched_at",
                    "raw_response_id",
                )
            },
            where=EVENTS.c.fetched_at <= stmt.excluded.fetched_at,
        ).returning(EVENTS.c.odds_event_id, INSERTED)
        counts += count_returned(conn.execute(stmt).all(), len(chunk))
    ids: dict[str, int] = {}
    wanted = [event.source_event_id for event in batch.events]
    for chunk in chunks(wanted):
        found = conn.execute(
            select(EVENTS.c.source_event_id, EVENTS.c.odds_event_id).where(
                EVENTS.c.source == batch.source, EVENTS.c.source_event_id.in_(chunk)
            )
        )
        ids.update({row.source_event_id: row.odds_event_id for row in found})
    return ids, counts


def insert_odds_snapshots(
    conn: Connection, batch: OddsBatch, event_ids: Mapping[str, int]
) -> tuple[int, int, int]:
    """Insert one row per price; return (inserted, already stored, dropped as started)."""
    provenance = batch.provenance
    rows: list[dict[str, Any]] = []
    started = 0
    for event in batch.events:
        if batch.captured_at >= event.commence_time:
            # Adapters already drop these; this guard keeps any OddsSource honest (7-2 CHECK).
            started += len(event.outcomes)
            continue
        for outcome in event.outcomes:
            rows.append(
                {
                    "odds_event_id": event_ids[event.source_event_id],
                    "source": batch.source,
                    "captured_at": batch.captured_at,
                    "capture_kind": batch.capture_kind,
                    "capture_label": batch.capture_label,
                    "commence_time": event.commence_time,
                    "bookmaker_key": outcome.bookmaker_key,
                    "bookmaker_last_update": outcome.bookmaker_last_update,
                    "market_key": outcome.market_key,
                    "outcome_name": outcome.outcome_name,
                    "outcome_side": outcome.outcome_side,
                    "price_decimal": outcome.price_decimal,
                    "point": outcome.point,
                    "fetched_at": provenance.fetched_at,
                    "raw_response_id": provenance.raw_response_id,
                }
            )
    inserted = 0
    for chunk in chunks(rows):
        stmt = (
            insert(SNAPSHOTS)
            .values(list(chunk))
            .on_conflict_do_nothing(constraint="uq_odds_snapshot")
            .returning(SNAPSHOTS.c.odds_snapshot_id)
        )
        inserted += len(conn.execute(stmt).all())
    return inserted, len(rows) - inserted, started


def map_odds_events(
    conn: Connection, source: str = THE_ODDS_API, odds_event_ids: Collection[int] | None = None
) -> MappingResult:
    """Fill game_id on unmatched events of a source (all, or only the given ids) by rule A-9."""
    home_alias = ALIASES.alias("home_alias")
    away_alias = ALIASES.alias("away_alias")
    stmt = (
        select(
            EVENTS.c.odds_event_id,
            EVENTS.c.source_event_id,
            EVENTS.c.home_team_name,
            EVENTS.c.away_team_name,
            EVENTS.c.commence_time,
            home_alias.c.team_id.label("home_team_id"),
            away_alias.c.team_id.label("away_team_id"),
        )
        .select_from(
            EVENTS.outerjoin(
                home_alias,
                and_(
                    home_alias.c.source == EVENTS.c.source,
                    home_alias.c.alias == EVENTS.c.home_team_name,
                ),
            ).outerjoin(
                away_alias,
                and_(
                    away_alias.c.source == EVENTS.c.source,
                    away_alias.c.alias == EVENTS.c.away_team_name,
                ),
            )
        )
        .where(EVENTS.c.source == source, EVENTS.c.game_id.is_(None))
        .order_by(EVENTS.c.odds_event_id)
    )
    if odds_event_ids is not None:
        if not odds_event_ids:
            return MappingResult()
        stmt = stmt.where(EVENTS.c.odds_event_id.in_(list(odds_event_ids)))
    events = conn.execute(stmt).all()
    issues: list[QualityIssue] = []
    matched = 0
    max_diff: float | None = None
    for event in events:
        key = event.source_event_id
        unknown = [
            name
            for name, team_id in (
                (event.home_team_name, event.home_team_id),
                (event.away_team_name, event.away_team_id),
            )
            if team_id is None
        ]
        if unknown:
            issues.append(
                QualityIssue("unknown_team_alias", key, f"no team_aliases({source}) for {unknown}")
            )
            continue
        candidates = _games_between(
            conn, event.home_team_id, event.away_team_id, event.commence_time
        )
        if len(candidates) == 1:
            game_id, start = candidates[0]
            conn.execute(
                update(EVENTS)
                .where(EVENTS.c.odds_event_id == event.odds_event_id)
                .values(game_id=game_id, match_method=MATCH_METHOD, matched_at=func.now())
            )
            matched += 1
            diff = abs((start - event.commence_time).total_seconds())
            max_diff = diff if max_diff is None else max(max_diff, diff)
            continue
        when = event.commence_time.isoformat()
        teams = f"{event.home_team_name} (home) vs {event.away_team_name} at {when}"
        if candidates:
            ids = [game_id for game_id, _ in candidates]
            issues.append(QualityIssue("ambiguous_game", key, f"{teams}: games {ids}"))
        elif _games_between(conn, event.away_team_id, event.home_team_id, event.commence_time):
            issues.append(
                QualityIssue("home_away_swapped", key, f"{teams}: only a reversed game exists")
            )
        else:
            issues.append(QualityIssue("no_matching_game", key, f"{teams}: no game within 6 h"))
    return MappingResult(len(events), matched, tuple(issues), max_diff)


def _games_between(
    conn: Connection, home_team_id: int, away_team_id: int, commence_time: datetime
) -> list[tuple[str, datetime]]:
    """(game_id, start_time_utc) of games with these home/away teams within MATCH_WINDOW."""
    rows = conn.execute(
        select(GAMES.c.game_id, GAMES.c.start_time_utc)
        .where(
            GAMES.c.home_team_id == home_team_id,
            GAMES.c.away_team_id == away_team_id,
            GAMES.c.start_time_utc.between(
                commence_time - MATCH_WINDOW, commence_time + MATCH_WINDOW
            ),
        )
        .order_by(GAMES.c.game_id)
    )
    return [(row.game_id, row.start_time_utc) for row in rows]


def remap_unmatched_events(engine: Engine, source: str = THE_ODDS_API) -> MappingResult:
    """Retry the mapping of every unmatched event, e.g. after the schedule was loaded."""
    with engine.begin() as conn:
        return map_odds_events(conn, source)


def anchor_to_raw(conn: Connection, batch: OddsBatch) -> OddsBatch:
    """A live batch re-timed to its raw row's fetched_at (first receipt of this content).

    The client reports the time of this call; when the content was already stored, the raw
    row keeps the earlier time, and the snapshot key must use that one so nothing repeats.
    """
    raw_id = batch.provenance.raw_response_id
    if batch.capture_kind != "live" or raw_id is None:
        return batch
    first_seen = conn.execute(
        select(RAW.c.fetched_at).where(RAW.c.raw_response_id == raw_id)
    ).scalar_one()
    if first_seen == batch.captured_at:
        return batch
    # Earlier than this call, so no event that had not started now had started then.
    return replace(
        batch,
        captured_at=first_seen,
        provenance=replace(batch.provenance, fetched_at=first_seen),
    )


def drop_duplicate_events(batch: OddsBatch) -> OddsBatch:
    """The batch with each event id kept at its first listing; later ones become issues.

    One INSERT ... ON CONFLICT cannot touch the same key twice, so a repeated id would fail
    the whole capture. Only the repeats are left out (P-019 review L1).
    """
    seen: set[str] = set()
    kept: list[OddsEventRecord] = []
    issues: list[QualityIssue] = []
    for event in batch.events:
        if event.source_event_id in seen:
            issues.append(
                QualityIssue(
                    "duplicate_event",
                    event.source_event_id,
                    f"listed again ({len(event.outcomes)} price(s)); first listing kept",
                )
            )
            continue
        seen.add(event.source_event_id)
        kept.append(event)
    if not issues:
        return batch
    return replace(batch, events=tuple(kept), issues=batch.issues + tuple(issues))


def store_odds_batch(conn: Connection, batch: OddsBatch) -> OddsLoadResult:
    """Upsert events, insert snapshots, then map this batch's still-unmatched events."""
    batch = drop_duplicate_events(batch)
    batch = anchor_to_raw(conn, batch)
    event_ids, events = upsert_odds_events(conn, batch)
    inserted, duplicate, started = insert_odds_snapshots(conn, batch, event_ids)
    mapping = map_odds_events(conn, batch.source, set(event_ids.values()))
    skipped = dict(batch.skipped)
    if started:
        skipped[SKIPPED_STARTED_OUTCOMES] = skipped.get(SKIPPED_STARTED_OUTCOMES, 0) + started
    return OddsLoadResult(
        captured_at=batch.captured_at,
        events=events,
        snapshots_inserted=inserted,
        snapshots_duplicate=duplicate,
        mapping=mapping,
        skipped=skipped,
        failed=dict(batch.rejected),
        issues=batch.issues + mapping.issues,
    )


def _record(run: RunTracker, batch: OddsBatch, result: OddsLoadResult) -> None:
    """Write a stored batch's counts, quota and issues into the run."""
    # A repeated event id is one item (stored once, its repeats are issues).
    listed = {event.source_event_id for event in batch.events}
    run.items_total = len(listed) + len(batch.rejected)
    for _ in listed:
        run.item_succeeded()
    for key, reason in result.failed.items():
        run.item_failed(key, reason)
    run.rows_written = result.events.written + result.snapshots_inserted
    run.detail.update(
        captured_at=result.captured_at.isoformat(),
        capture_label=batch.capture_label,
        raw_response_id=batch.provenance.raw_response_id,
        events=result.events.as_dict(),
        snapshots={
            "inserted": result.snapshots_inserted,
            "already_stored": result.snapshots_duplicate,
        },
        skipped=result.skipped,
        mapping=result.mapping.as_dict(),
        quota=dict(batch.quota),
    )
    record_issues(run, result.issues)
    rejected_prices = result.skipped.get(SKIPPED_REJECTED_OUTCOMES, 0)
    if rejected_prices:
        # A price we could not read is lost for this capture: say so loudly (D-034 3).
        run.mark_partial(f"{rejected_prices} price(s) could not be used")


def load_odds(
    source: OddsSource,
    engine: Engine,
    *,
    capture_label: str | None = None,
    stale_after_hours: float | None = None,
) -> OddsLoadResult:
    """Capture current odds once and store them as one ingestion run (job_type 'odds')."""
    with track_run(
        engine, JOB_ODDS, source.name, source=source.name, stale_after_hours=stale_after_hours
    ) as run:
        batch = source.fetch_odds(run, capture_label=capture_label)
        with engine.begin() as conn:
            result = store_odds_batch(conn, batch)
        _record(run, batch, result)
    return result


def load_odds_events(
    source: OddsSource, engine: Engine, *, stale_after_hours: float | None = None
) -> OddsLoadResult:
    """List upcoming events without prices (free for The Odds API) and map them."""
    with track_run(
        engine,
        JOB_ODDS_EVENTS,
        source.name,
        source=source.name,
        stale_after_hours=stale_after_hours,
    ) as run:
        batch = source.fetch_events(run)
        with engine.begin() as conn:
            result = store_odds_batch(conn, batch)
        _record(run, batch, result)
    return result


def seed_odds_team_aliases(
    conn: Connection,
    names: Mapping[str, str] = TEAM_NAME_SEED,
    *,
    source: str = THE_ODDS_API,
    note: str | None = TEAM_NAME_SEED_NOTE,
) -> UpsertCounts:
    """Upsert {tricode: provider name} into team_aliases; teams must be loaded first.

    fetched_at is the database time the seed was applied (schema 5-2). Applying the same
    seed again keeps the row count and only moves fetched_at.
    """
    team_ids = {
        row.tricode: row.team_id for row in conn.execute(select(TEAMS.c.tricode, TEAMS.c.team_id))
    }
    missing = sorted(set(names) - set(team_ids))
    if missing:
        raise ValueError(f"tricodes {missing} are not in teams; load teams first")
    rows: Sequence[dict[str, Any]] = [
        {
            "source": source,
            "alias": name,
            "team_id": team_ids[tricode],
            "fetched_at": func.now(),
            "note": note,
        }
        for tricode, name in sorted(names.items())
    ]
    if not rows:
        return UpsertCounts()
    stmt = insert(ALIASES).values(list(rows))
    stmt = stmt.on_conflict_do_update(
        index_elements=[ALIASES.c.source, ALIASES.c.alias],
        set_={
            "team_id": stmt.excluded.team_id,
            "fetched_at": stmt.excluded.fetched_at,
            "note": stmt.excluded.note,
        },
    ).returning(ALIASES.c.alias, INSERTED)
    return count_returned(conn.execute(stmt).all(), len(rows))
