"""A migrated temp DB (D-030) holding one clean, complete season that passes every check.

Baseline (committed once per test module): 30 teams with nba_api and the_odds_api aliases;
season 2023-24 with 1,230 final regular games (82 round-robin days, each team once a day)
scored by leaguegamelog, two stat rows each, and a schedule raw response with the same scores;
two final playoff games with stats; a final Cup final without stats (not required); one odds
event mapped to a game with one snapshot; one succeeded ingestion run. Tests that break data
do it inside a transaction they roll back (fixture `conn`), so the baseline never changes.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from nba_api.stats.static import teams as static_teams
from sqlalchemy import URL, Connection, Engine, text

from presagio.db.session import make_engine
from presagio.ingestion.loaders.odds import seed_odds_team_aliases
from presagio.ingestion.raw_store import canonical_json, request_hash, response_sha256

SEASON = "2023-24"
FETCHED = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
# First game day at 19:30 ET (23:30Z while daylight time lasts).
FIRST_TIP = datetime(2023, 10, 24, 23, 30, tzinfo=UTC)
DAYS = 82
PLAYOFF_GAMES = ("0042300101", "0042300102")
CUP_FINAL = "0062300001"
ODDS_EVENT = "evt-baseline"
# Box score columns the checks never read; filled with one constant.
STAT_COLUMNS = (
    "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "reb", "ast", "stl", "blk", "tov",
    "pf",
)  # fmt: skip


@dataclass(frozen=True)
class Baseline:
    """What the baseline contains, for tests to refer to."""

    tricodes: dict[int, str]
    regular_games: tuple[str, ...]
    games: dict[str, dict[str, Any]]  # game_id -> the inserted games row
    odds_game: str


def round_robin(team_ids: list[int], day: int) -> list[tuple[int, int]]:
    """Circle-method pairing for one day: every team plays exactly once (home, away)."""
    n = len(team_ids)
    shift = day % (n - 1)
    rotating = team_ids[1:][shift:] + team_ids[1:][:shift]
    order = [team_ids[0], *rotating]
    pairs = [(order[i], order[n - 1 - i]) for i in range(n // 2)]
    return [(a, b) if (day + i) % 2 == 0 else (b, a) for i, (a, b) in enumerate(pairs)]


def insert_raw(conn: Connection, source: str, endpoint: str, params: dict, body: Any) -> int:
    """Insert one raw_api_responses row (HTTP 200, JSON body) and return its id."""
    data = canonical_json(body).encode()
    return conn.execute(
        text(
            """
            INSERT INTO raw_api_responses (source, endpoint, request_params, request_hash,
                http_status, response_json, response_sha256, response_bytes, fetched_at,
                last_fetched_at)
            VALUES (:source, :endpoint, CAST(:params AS jsonb), :hash, 200, CAST(:body AS jsonb),
                    :sha, :bytes, :at, :at)
            RETURNING raw_response_id
            """
        ),
        {
            "source": source,
            "endpoint": endpoint,
            "params": canonical_json(params),
            "hash": request_hash(source, endpoint, params),
            "body": data.decode(),
            "sha": response_sha256(data),
            "bytes": len(data),
            "at": FETCHED,
        },
    ).scalar_one()


def game_row(game_id: str, start: datetime, home: int, away: int, n: int, **extra: Any) -> dict:
    """A final games row with deterministic scores (home wins on even n)."""
    home_score, away_score = (100 + n % 20, 95 + n % 17)
    return {
        "game_id": game_id,
        "season": SEASON,
        "start_time_utc": start,
        "status_code": 3,
        "status_text": "Final",
        "home_team_id": home,
        "away_team_id": away,
        "home_score": home_score,
        "away_score": away_score,
        "is_neutral": False,
        "source": "leaguegamelog",
        **extra,
    }


def insert_games(conn: Connection, rows: list[dict[str, Any]], raw_id: int) -> None:
    """Insert games rows (fetched now = FETCHED)."""
    conn.execute(
        text(
            """
            INSERT INTO games (game_id, season, start_time_utc, is_time_tbd, status_code,
                status_text, home_team_id, away_team_id, home_score, away_score, is_neutral,
                if_necessary, source, fetched_at, raw_response_id)
            VALUES (:game_id, :season, :start_time_utc, false, :status_code, :status_text,
                :home_team_id, :away_team_id, :home_score, :away_score, :is_neutral, false,
                :source, :fetched_at, :raw_response_id)
            """
        ),
        [{**r, "fetched_at": FETCHED, "raw_response_id": raw_id} for r in rows],
    )


def insert_stats(
    conn: Connection, game: dict[str, Any], raw_id: int, source: str = "leaguegamelog"
) -> None:
    """Two team_game_stats rows whose pts equal the game's score."""
    sides = (
        (game["home_team_id"], game["away_team_id"], True, game["home_score"]),
        (game["away_team_id"], game["home_team_id"], False, game["away_score"]),
    )
    conn.execute(
        text(
            f"""
            INSERT INTO team_game_stats (game_id, team_id, source, opponent_team_id, is_home,
                minutes, pts, {", ".join(STAT_COLUMNS)}, plus_minus, fetched_at, raw_response_id)
            VALUES (:game_id, :team_id, :source, :opponent, :is_home, 240, :pts,
                {", ".join(":" + c for c in STAT_COLUMNS)}, :pm, :fetched_at, :raw_id)
            """
        ),
        [
            {
                "game_id": game["game_id"],
                "team_id": team,
                "source": source,
                "opponent": opponent,
                "is_home": is_home,
                "pts": pts,
                **dict.fromkeys(STAT_COLUMNS, 10),
                "pm": 0,
                "fetched_at": FETCHED,
                "raw_id": raw_id,
            }
            for team, opponent, is_home, pts in sides
        ],
    )


def schedule_game(game: dict[str, Any]) -> dict[str, Any]:
    """The ScheduleLeagueV2 shape of one game (only the fields the checks read)."""
    return {
        "gameId": game["game_id"],
        "gameStatus": game["status_code"],
        "homeTeam": {"teamId": game["home_team_id"], "score": game["home_score"]},
        "awayTeam": {"teamId": game["away_team_id"], "score": game["away_score"]},
    }


def load_baseline(engine: Engine) -> Baseline:
    """Insert the baseline described in the module docstring and commit it."""
    teams = sorted(static_teams.get_teams(), key=lambda t: t["id"])
    team_ids = [t["id"] for t in teams]
    regular: list[dict[str, Any]] = []
    for day in range(DAYS):
        tip = FIRST_TIP + timedelta(days=day)
        for i, (home, away) in enumerate(round_robin(team_ids, day)):
            n = len(regular) + 1
            regular.append(game_row(f"00223{n:05d}", tip + timedelta(minutes=i), home, away, n))
    last_day = FIRST_TIP + timedelta(days=DAYS + 100)
    playoffs = [
        game_row(gid, last_day + timedelta(days=i), team_ids[0], team_ids[1], i)
        for i, gid in enumerate(PLAYOFF_GAMES)
    ]
    cup_final = game_row(
        CUP_FINAL, FIRST_TIP + timedelta(days=DAYS + 10), team_ids[2], team_ids[3], 7
    )
    cup_final.update(is_neutral=True, source="scheduleleaguev2")
    games = [*regular, *playoffs, cup_final]
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO teams (team_id, tricode, city, nickname, full_name, source, fetched_at)
                VALUES (:id, :abbreviation, :city, :nickname, :full_name, 'nba_api.static', :at)
                """
            ),
            [{**t, "at": FETCHED} for t in teams],
        )
        conn.execute(
            text(
                "INSERT INTO team_aliases (source, alias, team_id, fetched_at) "
                "VALUES ('nba_api', :full_name, :id, :at)"
            ),
            [{**t, "at": FETCHED} for t in teams],
        )
        seed_odds_team_aliases(conn)
        schedule = {"leagueSchedule": {"gameDates": [{"games": [schedule_game(g) for g in games]}]}}
        schedule_raw = insert_raw(
            conn, "nba_api", "scheduleleaguev2", {"Season": SEASON, "LeagueID": "00"}, schedule
        )
        log_raw = insert_raw(conn, "nba_api", "leaguegamelog", {"Season": SEASON}, {"rows": []})
        insert_games(conn, games, schedule_raw)
        for game in (*regular, *playoffs):
            insert_stats(conn, game, log_raw)
        odds_game = regular[100]
        odds_raw = insert_raw(conn, "the_odds_api", "odds", {"markets": "h2h"}, [])
        names = dict(
            conn.execute(
                text("SELECT team_id, alias FROM team_aliases WHERE source = 'the_odds_api'")
            ).all()
        )
        event_id = conn.execute(
            text(
                """
                INSERT INTO odds_events (source, source_event_id, home_team_name, away_team_name,
                    commence_time, game_id, match_method, matched_at, fetched_at, raw_response_id)
                VALUES ('the_odds_api', :event, :home, :away, :start, :game_id, 'alias+time',
                    :at, :at, :raw)
                RETURNING odds_event_id
                """
            ),
            {
                "event": ODDS_EVENT,
                "home": names[odds_game["home_team_id"]],
                "away": names[odds_game["away_team_id"]],
                "start": odds_game["start_time_utc"],
                "game_id": odds_game["game_id"],
                "at": FETCHED,
                "raw": odds_raw,
            },
        ).scalar_one()
        insert_snapshot(conn, event_id, odds_game["start_time_utc"], timedelta(hours=-2), odds_raw)
        conn.execute(
            text(
                """
                INSERT INTO ingestion_runs (job_type, source, scope_key, status, finished_at)
                VALUES ('schedule', 'nba_api', 'season=2023-24', 'succeeded', now())
                """
            )
        )
    return Baseline(
        {t["id"]: t["abbreviation"] for t in teams},
        tuple(g["game_id"] for g in regular),
        {g["game_id"]: g for g in games},
        odds_game["game_id"],
    )


def insert_snapshot(
    conn: Connection, event_id: int, commence: datetime, offset: timedelta, raw_id: int
) -> int:
    """One h2h home snapshot captured at commence + offset (negative = before the start)."""
    captured = commence + offset
    return conn.execute(
        text(
            """
            INSERT INTO odds_snapshots (odds_event_id, source, captured_at, capture_kind,
                commence_time, bookmaker_key, bookmaker_last_update, market_key, outcome_name,
                outcome_side, price_decimal, fetched_at, raw_response_id)
            VALUES (:event, 'the_odds_api', :captured, 'live', :commence, 'draftkings',
                :captured, 'h2h', 'Home', 'home', 1.9091, :captured, :raw)
            RETURNING odds_snapshot_id
            """
        ),
        {"event": event_id, "captured": captured, "commence": commence, "raw": raw_id},
    ).scalar_one()


@pytest.fixture(scope="module")
def quality_db(
    make_test_database: Callable[[], URL], alembic_config: Callable[[Connection], Config]
) -> Iterator[tuple[Engine, Baseline]]:
    """Fresh temp DB migrated to head with the baseline committed; dropped after the run."""
    engine = make_engine(make_test_database())
    with engine.begin() as conn:
        command.upgrade(alembic_config(conn), "head")
    baseline = load_baseline(engine)
    yield engine, baseline
    engine.dispose()


@pytest.fixture
def engine(quality_db: tuple[Engine, Baseline]) -> Engine:
    """Engine on the baseline DB."""
    return quality_db[0]


@pytest.fixture
def baseline(quality_db: tuple[Engine, Baseline]) -> Baseline:
    """What the baseline contains."""
    return quality_db[1]


@pytest.fixture
def conn(engine: Engine) -> Iterator[Connection]:
    """A connection in a transaction that is rolled back after the test (DDL included)."""
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()
