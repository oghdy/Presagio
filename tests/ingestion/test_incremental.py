"""presagio ingest-daily: dates and time zones, results, schedule, odds, resume (P-017 1-3).

Runs go CLI -> incremental -> loaders -> adapters -> IngestionClient(MockTransport serving
fixtures) -> temp database (D-030). No network. The base date and the client's clock are
injected; no test compares Python time with the database clock.
"""

import copy
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, func, select, text

import presagio.db.models  # noqa: F401  (registers every table on Base.metadata)
from presagio.cli import main
from presagio.config import Settings
from presagio.db.base import Base
from presagio.db.models import IngestionRun
from presagio.ingestion.backfill import et_day_bounds
from presagio.ingestion.client import IngestionClient, db_raw_store
from presagio.ingestion.incremental import (
    DailyRequest,
    DailySources,
    capture_times,
    korean_today,
    plan_daily,
    result_date,
    run_daily,
)
from presagio.ingestion.loaders import load_odds, load_season_schedule, load_teams
from presagio.ingestion.sources.nba_api import NbaApiScheduleSource, NbaApiTeamSource
from presagio.ingestion.sources.nba_api.scoreboard import NbaApiScoreboardSource
from presagio.ingestion.sources.nba_api.team_stats import NbaApiTeamStatsSource
from presagio.ingestion.sources.odds import TheOddsApiSource

from .helpers import FakeClock, fixture_bytes, fixture_json, fixture_name, html, json_bytes
from .odds_helpers import (
    FAKE_KEY,
    QUOTA,
    REAL_ODDS_FIXTURE,
    REAL_SAS_LAL,
    bump_prices,
    odds_json,
)

KST_OCT22 = date(2026, 10, 22)  # yesterday ET 10-21 (no games here), today ET 10-22
KST_OCT23 = date(2026, 10, 23)  # yesterday ET 10-22 (PHI-CLE, OKC-DEN), today ET 10-23
KST_OCT24 = date(2026, 10, 24)
# 16:00 KST on each base date (INGEST_DAILY_TIME_KST default), when the full run is planned.
RUN_AT = {
    d: datetime(d.year, d.month, d.day, 7, 0, tzinfo=UTC) for d in (KST_OCT22, KST_OCT23, KST_OCT24)
}
PHI_CLE, OKC_DEN = "0022600006", "0022600007"
PHI, CLE, OKC, DEN = 1610612755, 1610612739, 1610612760, 1610612743
LOG_2026 = "leaguegamelog__2026-27_RegularSeason"
STATS_QUERY = (
    "SELECT game_id, team_id, source, pts, tov, raw_response_id FROM team_game_stats ORDER BY 1, 2"
)
# What a game "is" after the daily run; fetched_at/source/raw_response_id record the last fetch.
RESULT_COLUMNS = (
    "game_id, season, start_time_utc, status_code, status_text, home_team_id, away_team_id, "
    "home_score, away_score, is_neutral, if_necessary, postponed_status"
)


# --- dates and time zones (completion criterion 1, no database) ---------------------------


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 10, 22, 14, 59, 59, tzinfo=UTC), date(2026, 10, 22)),  # 23:59:59 KST
        (datetime(2026, 10, 22, 15, 0, 0, tzinfo=UTC), date(2026, 10, 23)),  # 00:00 KST
        (datetime(2026, 12, 31, 15, 0, 0, tzinfo=UTC), date(2027, 1, 1)),  # new year in Korea
        # 23:30 ET on 2026-10-22 is already 12:30 the next day in Korea.
        (datetime(2026, 10, 23, 3, 30, tzinfo=UTC), date(2026, 10, 23)),
    ],
)
def test_base_date_defaults_to_today_in_korea(now: datetime, expected: date) -> None:
    """D is the Asia/Seoul calendar date of the moment, whatever the US date is."""
    assert korean_today(now) == expected


def test_base_date_needs_an_aware_time() -> None:
    """A naive time has no zone to convert from."""
    with pytest.raises(ValueError, match="timezone-aware"):
        korean_today(datetime(2026, 10, 22, 12, 0))


def test_yesterday_and_today_are_et_dates_d_minus_1_and_d() -> None:
    """Results for ET D-1, schedule for ET D, across month and year ends."""
    request = DailyRequest(date(2027, 1, 1))
    assert request.yesterday_et == date(2026, 12, 31) and request.today_et == date(2027, 1, 1)
    assert result_date(date(2028, 3, 1)) == date(2028, 2, 29)
    assert DailyRequest(KST_OCT23).scope_key() == "date_kst=2026-10-23"
    assert DailyRequest(KST_OCT23, odds_only=True).scope_key() == "date_kst=2026-10-23;odds_only"


@pytest.mark.parametrize(
    ("start_utc", "et_date"),
    [
        # 22:30 EST on 03-07 is 03:30Z on 03-08: still the 03-07 ET game.
        (datetime(2026, 3, 8, 3, 30, tzinfo=UTC), date(2026, 3, 7)),
        # 22:30 EDT on 03-08 (DST began 02:00 that day) is 02:30Z on 03-09.
        (datetime(2026, 3, 9, 2, 30, tzinfo=UTC), date(2026, 3, 8)),
        # 23:30 EDT on 10-31 is 03:30Z on 11-01; 23:30 EST on 11-01 is 04:30Z on 11-02.
        (datetime(2026, 11, 1, 3, 30, tzinfo=UTC), date(2026, 10, 31)),
        (datetime(2026, 11, 2, 4, 30, tzinfo=UTC), date(2026, 11, 1)),
        # 00:15 ET is the new ET day, though it is still the previous day in UTC.
        (datetime(2026, 10, 23, 4, 15, tzinfo=UTC), date(2026, 10, 23)),
    ],
)
def test_late_games_fall_on_their_eastern_date(start_utc: datetime, et_date: date) -> None:
    """The UTC window of an ET date holds exactly the games that started on it (DST too)."""
    for day in (et_date - timedelta(days=1), et_date, et_date + timedelta(days=1)):
        lower, upper = et_day_bounds(day, day)
        assert (lower <= start_utc < upper) == (day == et_date), day


@pytest.mark.parametrize(
    ("base", "closing_utc"),
    [
        (date(2026, 3, 7), datetime(2026, 3, 7, 23, 45, tzinfo=UTC)),  # EST: 18:45 = 23:45Z
        (date(2026, 3, 8), datetime(2026, 3, 8, 22, 45, tzinfo=UTC)),  # EDT from 02:00 ET
        (date(2026, 10, 31), datetime(2026, 10, 31, 22, 45, tzinfo=UTC)),  # still EDT
        (date(2026, 11, 1), datetime(2026, 11, 1, 23, 45, tzinfo=UTC)),  # EST again
    ],
)
def test_capture_times_follow_both_zones(base: date, closing_utc: datetime) -> None:
    """Full run at 16:00 KST (no DST in Korea) = 07:00Z; closing times move with US DST."""
    captures = capture_times(base, Settings(database_url="postgresql://x/y"))
    full = [c.at_utc for c in captures if c.label == "full run"]
    closing = [c.at_utc for c in captures if c.label == "odds-only"]
    assert full == [datetime(base.year, base.month, base.day, 7, 0, tzinfo=UTC)]
    assert closing[0] == closing_utc
    assert [c - closing[0] for c in closing] == [
        timedelta(0),
        timedelta(hours=1),
        timedelta(hours=3),
    ]


def test_full_run_time_is_after_yesterdays_games() -> None:
    """16:00 KST is 03:00 ET (EDT) / 02:00 ET (EST) of ET date D: D-1 games have ended."""
    for base in (date(2026, 10, 22), date(2027, 1, 15)):
        (full,) = [
            c
            for c in capture_times(base, Settings(database_url="postgresql://x/y"))
            if c.label == "full run"
        ]
        _, yesterday_end = et_day_bounds(result_date(base), result_date(base))
        assert full.at_utc - yesterday_end >= timedelta(hours=2)


# --- fixtures: nba_api and The Odds API over MockTransport, raw in the temp DB ----------------


def scoreboard(day: date, *games: dict[str, Any]) -> bytes:
    """A ScoreboardV3 body for an ET date with the given game objects."""
    data = fixture_json("scoreboardv3__2026-10-22")
    data["scoreboard"]["gameDate"] = day.isoformat()
    data["scoreboard"]["games"] = list(games)
    return json.dumps(data).encode()


def board_game(game_id: str) -> dict[str, Any]:
    """A copy of one game of the 2026-10-22 scoreboard fixture."""
    data = fixture_json("scoreboardv3__2026-10-22")
    return copy.deepcopy(next(g for g in data["scoreboard"]["games"] if g["gameId"] == game_id))


def final(game: dict[str, Any], home: int, away: int, text_: str = "Final") -> dict[str, Any]:
    """The game as the scoreboard shows it after the final buzzer."""
    game = copy.deepcopy(game)
    game.update(gameStatus=3, gameStatusText=text_, period=4)
    game["homeTeam"]["score"], game["awayTeam"]["score"] = home, away
    return game


def live(game: dict[str, Any]) -> dict[str, Any]:
    """The game in progress (status 2, running score)."""
    game = copy.deepcopy(game)
    game.update(gameStatus=2, gameStatusText="Q3 5:12")
    game["homeTeam"]["score"], game["awayTeam"]["score"] = 70, 66
    return game


def game_log(*games: tuple[str, int, str, int, str, int, int]) -> bytes:
    """2026-27 regular game log with only the given games: (id, home, home abbr, away, ...)."""
    data = fixture_json(LOG_2026)
    headers = data["resultSets"][0]["headers"]
    home_row, away_row = data["resultSets"][0]["rowSet"][:2]  # DET vs. BOS, BOS @ DET
    rows = []
    for game_id, home, home_abbr, away, away_abbr, home_pts, away_pts in games:
        for template, team, abbr, matchup, pts, other in (
            (home_row, home, home_abbr, f"{home_abbr} vs. {away_abbr}", home_pts, away_pts),
            (away_row, away, away_abbr, f"{away_abbr} @ {home_abbr}", away_pts, home_pts),
        ):
            row = dict(zip(headers, template, strict=True))
            row.update(
                GAME_ID=game_id,
                TEAM_ID=team,
                TEAM_ABBREVIATION=abbr,
                MATCHUP=matchup,
                GAME_DATE="2026-10-22",
                PTS=pts,
                WL="W" if pts > other else "L",
                PLUS_MINUS=pts - other,
            )
            rows.append([row[h] for h in headers])
    data["resultSets"][0]["rowSet"] = rows
    return json.dumps(data).encode()


PHI_CLE_FINAL = (PHI_CLE, PHI, "PHI", CLE, "CLE", 110, 104)
OKC_DEN_FINAL = (OKC_DEN, OKC, "OKC", DEN, "DEN", 112, 115)


@dataclass
class World:
    """Fixture-serving sources and what they were asked; edit boards/logs/odds between runs."""

    clock: FakeClock
    boards: dict[str, bytes] = field(default_factory=dict)  # GameDate -> body
    overrides: dict[str, bytes] = field(default_factory=dict)  # fixture name -> body
    odds: Any = None  # /odds JSON; None = no key configured
    blocked: dict[str, int] = field(default_factory=dict)  # request name -> status
    seen: list[str] = field(default_factory=list)
    client: IngestionClient | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        """Route stats.nba.com and The Odds API requests to the bodies above."""
        self.clock.advance(0.25)
        endpoint = request.url.path.rsplit("/", 1)[-1]
        if request.url.host == "stats.nba.com" and endpoint == "scoreboardv3":
            name = f"scoreboardv3__{request.url.params['GameDate']}"
        elif endpoint == "odds":
            name = "odds"
        else:
            name = fixture_name(request)
        self.seen.append(name)
        if name in self.blocked:
            return html(self.blocked[name])
        if name == "odds":
            return httpx.Response(
                200,
                content=json.dumps(self.odds).encode(),
                headers={"Content-Type": "application/json", **QUOTA},
            )
        if name.startswith("scoreboardv3__"):
            day = date.fromisoformat(name.split("__")[1])
            if day.isoformat() in self.boards:
                return json_bytes(200, self.boards[day.isoformat()])
            if day == date(2026, 10, 22):  # the real fixture: PHI-CLE and OKC-DEN scheduled
                return json_bytes(200, fixture_bytes(name))
            return json_bytes(200, scoreboard(day))  # an off day
        return json_bytes(200, self.overrides.get(name) or fixture_bytes(name))

    def sources(self, engine: Engine) -> DailySources:
        """What main(sources=...) builds the run from."""
        assert self.client is not None
        odds = TheOddsApiSource(self.client, FAKE_KEY) if self.odds is not None else None
        return DailySources(
            NbaApiTeamSource(self.client),
            NbaApiScoreboardSource(self.client),
            NbaApiTeamStatsSource(self.client),
            odds,
        )

    def calls(self, prefix: str = "") -> list[str]:
        """Requests seen so far whose name starts with prefix."""
        return [name for name in self.seen if name.startswith(prefix)]


@pytest.fixture
def db(engine: Engine) -> Engine:
    """Temp DB with teams emptied too (engine empties raw, runs and everything that cascades)."""
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE teams, team_aliases CASCADE"))
    return engine


@pytest.fixture
def world(db: Engine, make_client: Callable[..., IngestionClient], clock: FakeClock) -> World:
    """Teams and the 2026-27 schedule loaded (as a backfill would); odds on with the real fixture.

    The schedule fixture has PHI-CLE (0022600006) on ET 10-22 but not OKC-DEN (0022600007),
    which only the scoreboard lists: the daily run must insert it.
    """
    w = World(clock)
    clock.wall = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)  # the backfill ran before day 1
    w.client = make_client(w.handler, store=db_raw_store(db))
    load_teams(NbaApiTeamSource(w.client), db)
    load_season_schedule(NbaApiScheduleSource(w.client), db, "2026-27")
    w.odds = odds_json(REAL_ODDS_FIXTURE)
    w.seen.clear()
    return w


def ingest(db: Engine, w: World, base: date | None, *args: str, now: datetime | None = None) -> int:
    """Run `presagio ingest-daily [--date base] <args>` in-process at the base date's run time."""
    moment = now or (RUN_AT[base] if base in RUN_AT else datetime.now(UTC))
    w.clock.wall = moment  # the client's receive times follow the run time
    argv = ["ingest-daily", *(["--date", base.isoformat()] if base else []), *args]
    return main(argv, engine=db, sources=w.sources, now=moment, setup_logging=False)


def table_counts(db: Engine) -> dict[str, int]:
    """Row count of every table."""
    with db.connect() as conn:
        return {
            t.name: conn.execute(select(func.count()).select_from(t)).scalar_one()
            for t in Base.metadata.sorted_tables
        }


def runs(db: Engine, **where: Any) -> list[Any]:
    """ingestion_runs rows (oldest first), filtered by column values."""
    table = IngestionRun.__table__
    stmt = select(table).order_by(table.c.run_id)
    for name, value in where.items():
        stmt = stmt.where(table.c[name] == value)
    with db.connect() as conn:
        return conn.execute(stmt).all()


def game_row(db: Engine, game_id: str) -> Any:
    """One games row."""
    with db.connect() as conn:
        return conn.execute(text("SELECT * FROM games WHERE game_id = :g"), {"g": game_id}).one()


def rows(db: Engine, sql: str, **params: Any) -> list[tuple[Any, ...]]:
    """All rows of a query as tuples."""
    with db.connect() as conn:
        return [tuple(r) for r in conn.execute(text(sql), params)]


def finish_yesterday(w: World) -> None:
    """ET 10-22 is over: both games final on the scoreboard and in the game log."""
    w.boards["2026-10-22"] = scoreboard(
        date(2026, 10, 22),
        final(board_game(PHI_CLE), 110, 104),
        final(board_game(OKC_DEN), 112, 115, "Final/OT"),
    )
    w.overrides[LOG_2026] = game_log(PHI_CLE_FINAL, OKC_DEN_FINAL)


# --- criterion 2: yesterday's scheduled games become final with scores and box scores ----------


def test_scheduled_game_becomes_final_with_scores_and_box_scores(db: Engine, world: World) -> None:
    """Day 1 loads today's schedule; day 2 turns those games final and stores team stats."""
    assert ingest(db, world, KST_OCT22) == 0
    assert world.calls() == ["scoreboardv3__2026-10-21", "scoreboardv3__2026-10-22", "odds"]
    phi = game_row(db, PHI_CLE)
    assert (phi.status_code, phi.home_score, phi.source) == (1, None, "scoreboardv3")
    # Columns the scoreboard does not have keep the schedule's values (6-1).
    assert (phi.season, phi.postponed_status) == ("2026-27", "N")
    okc = game_row(db, OKC_DEN)  # not in the schedule fixture: inserted by the scoreboard
    assert (okc.season, okc.status_code, okc.postponed_status) == ("2026-27", 1, None)
    assert rows(db, "SELECT count(*) FROM team_game_stats") == [(0,)]

    finish_yesterday(world)
    world.seen.clear()
    assert ingest(db, world, KST_OCT23) == 0
    assert world.calls() == [
        "scoreboardv3__2026-10-22",
        LOG_2026,
        "scoreboardv3__2026-10-23",
        "odds",
    ]
    phi, okc = game_row(db, PHI_CLE), game_row(db, OKC_DEN)
    assert (phi.status_code, phi.status_text, phi.home_score, phi.away_score) == (
        3,
        "Final",
        110,
        104,
    )
    # The game log keeps the scoreboard's own final text (6-1); its fetch is the latest source.
    assert (okc.status_code, okc.status_text, okc.home_score, okc.away_score) == (
        3,
        "Final/OT",
        112,
        115,
    )
    assert phi.source == "leaguegamelog"
    assert rows(
        db,
        "SELECT game_id, team_id, is_home, pts FROM team_game_stats_primary "
        "ORDER BY game_id, is_home DESC",
    ) == [
        (PHI_CLE, PHI, True, 110),
        (PHI_CLE, CLE, False, 104),
        (OKC_DEN, OKC, True, 112),
        (OKC_DEN, DEN, False, 115),
    ]
    (unit,) = runs(db, job_type="schedule", scope_key="date_et=2026-10-22")
    assert (unit.status, unit.items_total, unit.items_succeeded, unit.items_failed) == (
        "succeeded",
        2,
        2,
        0,
    )
    assert unit.target_date_et == date(2026, 10, 22) and unit.api_calls == 1
    (parent,) = runs(db, job_type="incremental", scope_key="date_kst=2026-10-23")
    assert parent.status == "succeeded" and parent.target_date_et == date(2026, 10, 23)
    children = runs(db, parent_run_id=parent.run_id)
    assert [(c.job_type, c.scope_key) for c in children] == [
        ("schedule", "date_et=2026-10-22"),
        ("gamelog", "season=2026-27;season_type=Regular Season"),
        ("schedule", "date_et=2026-10-23;phase=upcoming"),
        ("odds", "the_odds_api"),
    ]
    assert parent.api_calls == 0 and parent.error_detail["totals"]["api_calls"] == 4
    assert {c.code_version for c in children} == {parent.code_version}


# --- criterion 3: the same day twice --------------------------------------------------------------


def test_same_day_twice_gives_the_same_result(db: Engine, world: World) -> None:
    """Second run: same games and stats rows, no new raw rows; only odds may add snapshots."""
    ingest(db, world, KST_OCT22)
    finish_yesterday(world)
    assert ingest(db, world, KST_OCT23) == 0
    games_before = rows(db, f"SELECT {RESULT_COLUMNS} FROM games ORDER BY game_id")
    stats_before = rows(db, STATS_QUERY)
    counts = table_counts(db)
    raw_before = rows(db, "SELECT raw_response_id, fetch_count FROM raw_api_responses ORDER BY 1")

    # Same day, an hour later, odds unchanged: the run adds no data rows at all.
    world.seen.clear()
    assert ingest(db, world, KST_OCT23, now=RUN_AT[KST_OCT23] + timedelta(hours=1)) == 0
    # Yesterday is done (resume) and complete: no results or game log call (B1).
    assert world.calls() == ["scoreboardv3__2026-10-23", "odds"]
    after = table_counts(db)
    assert {k: v for k, v in after.items() if k != "ingestion_runs"} == {
        k: v for k, v in counts.items() if k != "ingestion_runs"
    }
    assert rows(db, "SELECT count(*) FROM games") == [(len(games_before),)]
    assert rows(db, STATS_QUERY) == stats_before
    # Same content -> same raw row, its fetch_count goes up (A-1); no new versions.
    raw_after = dict(rows(db, "SELECT raw_response_id, fetch_count FROM raw_api_responses"))
    assert set(raw_after) == {r for r, _ in raw_before}
    assert sum(raw_after.values()) == sum(c for _, c in raw_before) + 2

    # --force: yesterday and its game log again; still the same rows (upserts).
    world.seen.clear()
    assert ingest(db, world, KST_OCT23, "--force", now=RUN_AT[KST_OCT23] + timedelta(hours=2)) == 0
    assert world.calls("scoreboardv3__2026-10-22") and world.calls(LOG_2026)
    assert table_counts(db)["games"] == counts["games"]
    assert table_counts(db)["team_game_stats"] == counts["team_game_stats"]
    assert table_counts(db)["raw_api_responses"] == counts["raw_api_responses"]
    assert rows(db, f"SELECT {RESULT_COLUMNS} FROM games ORDER BY game_id") == games_before


def test_odds_snapshots_grow_only_when_prices_change(db: Engine, world: World) -> None:
    """Odds are snapshots: a new capture with new prices adds rows; the same response adds none."""
    ingest(db, world, KST_OCT22)
    first = rows(db, "SELECT count(*), count(DISTINCT captured_at) FROM odds_snapshots")[0]
    events = table_counts(db)["odds_events"]
    assert first[0] > 0 and first[1] == 1

    world.seen.clear()
    later = RUN_AT[KST_OCT22] + timedelta(hours=1)
    assert ingest(db, world, KST_OCT22, now=later) == 0  # identical /odds body
    assert rows(db, "SELECT count(*) FROM odds_snapshots")[0][0] == first[0]

    world.odds = bump_prices(world.odds, 0.01)
    assert ingest(db, world, KST_OCT22, now=later + timedelta(hours=1)) == 0
    total, captures = rows(db, "SELECT count(*), count(DISTINCT captured_at) FROM odds_snapshots")[
        0
    ]
    assert total == 2 * first[0] and captures == 2  # one new row per price, at its own captured_at
    assert table_counts(db)["odds_events"] == events  # events stay one row each
    labels = rows(db, "SELECT DISTINCT capture_label FROM odds_snapshots")
    assert labels == [("prediction",)]


# --- partial dates and retries (schema ch. 4) ----------------------------------------------------


def test_unfinished_game_makes_the_date_partial_and_the_next_run_retries_it(
    db: Engine, world: World
) -> None:
    """A game still on at run time leaves ET 10-22 partial; the next day fetches 10-22 again."""
    ingest(db, world, KST_OCT22)
    world.boards["2026-10-22"] = scoreboard(
        date(2026, 10, 22), final(board_game(PHI_CLE), 110, 104), live(board_game(OKC_DEN))
    )
    world.overrides[LOG_2026] = game_log(PHI_CLE_FINAL)
    assert ingest(db, world, KST_OCT23) == 1
    (unit,) = runs(db, job_type="schedule", scope_key="date_et=2026-10-22")
    assert unit.status == "partial"
    assert unit.error_detail["remaining"] == {OKC_DEN: "not final (status 2, 'Q3 5:12')"}
    assert (unit.items_succeeded, unit.items_total) == (1, 2)
    okc = game_row(db, OKC_DEN)
    assert (okc.status_code, okc.home_score) == (2, None)  # running score is not a result

    finish_yesterday(world)
    world.seen.clear()
    assert ingest(db, world, KST_OCT24) == 0
    assert world.calls("scoreboardv3") == [
        "scoreboardv3__2026-10-22",  # retry, oldest first
        "scoreboardv3__2026-10-23",  # yesterday
        "scoreboardv3__2026-10-24",  # today
    ]
    latest = runs(db, job_type="schedule", scope_key="date_et=2026-10-22")[-1]
    assert latest.status == "succeeded"
    assert game_row(db, OKC_DEN).away_score == 115


def test_final_game_missing_from_the_game_log_keeps_the_date_partial(
    db: Engine, world: World
) -> None:
    """Final on the scoreboard but no game log lines yet (lag): partial, not succeeded (Q9)."""
    ingest(db, world, KST_OCT22)
    finish_yesterday(world)
    world.overrides[LOG_2026] = game_log(PHI_CLE_FINAL)  # OKC-DEN not in the log yet
    assert ingest(db, world, KST_OCT23) == 1
    (unit,) = runs(db, job_type="schedule", scope_key="date_et=2026-10-22")
    assert unit.status == "partial"
    assert unit.error_detail["remaining"] == {OKC_DEN: "final, 0 of 2 game log team lines"}
    okc = game_row(db, OKC_DEN)
    assert (okc.status_code, okc.home_score) == (3, 112)  # the scoreboard result is stored


def test_missed_day_is_fetched_by_the_next_run(db: Engine, world: World) -> None:
    """No run on D=10-23: the 10-24 run fetches ET 10-22 too (its schedule was loaded)."""
    ingest(db, world, KST_OCT22)
    finish_yesterday(world)
    world.seen.clear()
    assert ingest(db, world, KST_OCT24) == 0
    assert world.calls("scoreboardv3") == [
        "scoreboardv3__2026-10-22",
        "scoreboardv3__2026-10-23",
        "scoreboardv3__2026-10-24",
    ]
    (unit,) = runs(db, job_type="schedule", scope_key="date_et=2026-10-22")
    assert unit.status == "succeeded"


def test_first_run_does_not_look_back_at_unknown_dates(db: Engine, world: World) -> None:
    """Dates the daily job never saw are the backfill's job: no extra calls on a first run."""
    ingest(db, world, KST_OCT24)
    assert world.calls("scoreboardv3") == ["scoreboardv3__2026-10-23", "scoreboardv3__2026-10-24"]


# --- teams, odds, modes ------------------------------------------------------------------------


def age_teams_runs(db: Engine, days: int) -> None:
    """Move every teams run the given number of days into the past (DB clock)."""
    with db.begin() as conn:
        conn.execute(
            text(
                "UPDATE ingestion_runs SET started_at = started_at - make_interval(days => :d), "
                "finished_at = finished_at - make_interval(days => :d) WHERE job_type = 'teams'"
            ),
            {"d": days},
        )


def test_teams_are_refreshed_once_a_week(db: Engine, world: World) -> None:
    """Teams loaded a day ago: no call (3 nba_api calls a day); 8 days ago: reloaded once."""
    age_teams_runs(db, 1)
    assert ingest(db, world, KST_OCT22) == 0
    assert world.calls("commonteamyears") == []
    assert len(world.calls("scoreboardv3")) == 2

    age_teams_runs(db, 7)  # now 8 days old: older than TEAMS_REFRESH_HOURS (168 h)
    world.seen.clear()
    assert ingest(db, world, KST_OCT22, now=RUN_AT[KST_OCT22] + timedelta(hours=1)) == 0
    assert world.calls("commonteamyears") == ["commonteamyears__00"]
    world.seen.clear()
    assert ingest(db, world, KST_OCT22, now=RUN_AT[KST_OCT22] + timedelta(hours=2)) == 0
    assert world.calls("commonteamyears") == []


def test_no_odds_key_skips_the_odds_step(db: Engine, world: World, capsys) -> None:
    """Without ODDS_API_KEY the odds step is 'skipped', recorded, and the run still succeeds."""
    world.odds = None
    assert ingest(db, world, KST_OCT22) == 0
    assert "odds" not in world.seen
    (parent,) = runs(db, job_type="incremental")
    steps = {s["step"]: s["status"] for s in parent.error_detail["steps"]}
    assert steps["odds"] == "skipped"
    assert runs(db, job_type="odds") == []
    assert "ODDS_API_KEY is not set" in capsys.readouterr().out


def test_odds_only_makes_no_nba_api_call(db: Engine, world: World) -> None:
    """--odds-only: one /odds call (closing capture), nothing from nba_api."""
    assert ingest(db, world, KST_OCT22, "--odds-only") == 0
    assert world.seen == ["odds"]
    (parent,) = runs(db, job_type="incremental")
    assert parent.scope_key == "date_kst=2026-10-22;odds_only"
    assert rows(db, "SELECT DISTINCT capture_label FROM odds_snapshots") == [("pre_tip",)]


def test_odds_aliases_are_seeded_when_missing(db: Engine, world: World) -> None:
    """A database that never had the P-019 seed gets it, so today's events map to games."""
    assert rows(db, "SELECT count(*) FROM team_aliases WHERE source = 'the_odds_api'") == [(0,)]
    ingest(db, world, KST_OCT22)
    assert rows(db, "SELECT count(*) FROM team_aliases WHERE source = 'the_odds_api'") == [(30,)]
    mapped = dict(
        rows(db, "SELECT game_id, count(*) FROM odds_events WHERE game_id IS NOT NULL GROUP BY 1")
    )
    assert mapped == {PHI_CLE: 1, OKC_DEN: 1}


def test_dry_run_writes_nothing(db: Engine, world: World, capsys) -> None:
    """--dry-run prints dates, planned times and steps; no request, no row."""
    before = table_counts(db)
    assert ingest(db, world, KST_OCT23, "--dry-run") == 0
    assert world.seen == [] and table_counts(db) == before
    out = capsys.readouterr().out
    assert "Base date D = 2026-10-23 (Korea, Asia/Seoul)" in out
    assert "yesterday's games: ET 2026-10-22 = UTC [10-22 04:00, 10-23 04:00) 24 h" in out
    assert "full run   10-23 16:00 KST  10-23 03:00 ET  10-23 07:00 UTC" in out
    assert "date_et=2026-10-22" in out and "Would make: 4 call(s)" in out


def test_default_date_is_today_in_korea(db: Engine, world: World, capsys) -> None:
    """Without --date, 23:30 ET on 10-22 (12:30 KST 10-23) means D = 2026-10-23."""
    assert ingest(db, world, None, "--dry-run", now=datetime(2026, 10, 23, 3, 30, tzinfo=UTC)) == 0
    assert "Base date D = 2026-10-23" in capsys.readouterr().out


# --- failures and blocks ----------------------------------------------------------------------


def test_a_failed_step_does_not_stop_the_next(db: Engine, world: World) -> None:
    """Today's scoreboard answers 404: that step fails, odds still run, exit 1."""
    world.blocked["scoreboardv3__2026-10-22"] = 404
    assert ingest(db, world, KST_OCT22) == 1
    assert world.calls() == ["scoreboardv3__2026-10-21", "scoreboardv3__2026-10-22", "odds"]
    (parent,) = runs(db, job_type="incremental")
    steps = {s["step"]: s["status"] for s in parent.error_detail["steps"]}
    assert steps == {
        "teams": "skipped",
        "results": "succeeded",
        "gamelog": "skipped",
        "upcoming": "failed",
        "odds": "succeeded",
        "checks": "skipped",
    }
    assert parent.status == "partial"


def test_block_stops_everything_with_exit_2(db: Engine, world: World, capsys) -> None:
    """403 on yesterday's scoreboard: nothing else is called, parent and unit are aborted."""
    ingest(db, world, KST_OCT22)
    world.blocked["scoreboardv3__2026-10-22"] = 403
    world.seen.clear()
    assert ingest(db, world, KST_OCT23) == 2
    assert world.seen == ["scoreboardv3__2026-10-22"]
    (parent,) = runs(db, job_type="incremental", scope_key="date_kst=2026-10-23")
    assert parent.status == "aborted"
    (unit,) = runs(db, job_type="schedule", scope_key="date_et=2026-10-22")
    assert unit.status == "aborted"
    steps = {s["step"]: s["status"] for s in parent.error_detail["steps"]}
    assert steps["upcoming"] == "not_reached" and steps["odds"] == "not_reached"
    out = capsys.readouterr().out
    assert "Stopped: the source blocked us" in out and "Exit code 2" in out


def test_block_during_the_game_log_closes_open_dates(db: Engine, world: World) -> None:
    """429 on the game log: the open date unit is closed aborted, not left running."""
    ingest(db, world, KST_OCT22)
    finish_yesterday(world)
    world.blocked[LOG_2026] = 429
    assert ingest(db, world, KST_OCT23) == 2
    assert {r.status for r in runs(db)} <= {"succeeded", "aborted", "partial", "failed"}
    (unit,) = runs(db, job_type="schedule", scope_key="date_et=2026-10-22")
    assert unit.status == "aborted"


def test_checks_hook_runs_last(db: Engine, world: World) -> None:
    """P-020 plugs checks in at the end; a problem makes the step partial and exit non-zero."""
    calls: list[DailyRequest] = []

    def check(engine: Engine, request: DailyRequest) -> list[str]:
        """Record the call and report one problem."""
        calls.append(request)
        return ["2 games without team stats"]

    request = DailyRequest(KST_OCT22)
    world.clock.wall = RUN_AT[KST_OCT22]
    plan = plan_daily(db, request, odds_enabled=True, checks=[check])
    report = run_daily(db, plan, world.sources(db), checks=[check])
    assert calls == [request]
    assert report.results[-1].step == "checks" and report.results[-1].status == "partial"
    assert report.exit_code == 1


# --- odds loader: a repeated event id (P-019 review L1) -------------------------------------------


def test_duplicate_event_id_drops_only_the_repeat(db: Engine, world: World) -> None:
    """The same id listed twice: first listing stored, repeat is an issue, the rest loads."""
    world.odds.append(copy.deepcopy(next(e for e in world.odds if e["id"] == REAL_SAS_LAL)))
    world.clock.wall = RUN_AT[KST_OCT22]
    assert world.client is not None
    result = load_odds(TheOddsApiSource(world.client, FAKE_KEY), db)
    assert [i.kind for i in result.issues if i.kind == "duplicate_event"] == ["duplicate_event"]
    assert table_counts(db)["odds_events"] == 3
    (run,) = runs(db, job_type="odds")
    assert run.status == "succeeded" and run.items_total == 3 and run.items_succeeded == 3
    kinds = [i["kind"] for i in run.error_detail["quality_issues"]]
    assert "duplicate_event" in kinds
