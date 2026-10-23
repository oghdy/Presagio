"""The small four-team league the evaluation tests load into a migrated temp DB (D-030).

Contents: 30 teams (nba_api and the_odds_api aliases), teams A, B, C, D
playing on every other ET date (A-B 19:30 ET, C-D 20:00 ET, home side alternating):

- 2023-24: 85 regular dates from 2023-10-24, plus the T4 pair (2024-01-10 C-D moved to
  23:00 ET and an extra 2024-01-11 A-C game), a Cup final without stats on 2023-12-10, a
  play-in and two playoff games.
- 2024-25: 85 regular dates from 2024-10-22 and one playoff game.
- 2025-26 (holdout): 10 regular dates from 2025-10-21.
- 2026-27: 10-20 A-B (unmatched odds event), 10-21 C-D (market available: three books at
  10-20 21:45 ET, closes 45 min before tip-off), 10-22 A-C (one book, last quote 21 h before
  tip-off: pred_only), 10-23 B-D not final.

Every final game except the Cup final has two leaguegamelog stat rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from nba_api.stats.static import teams as static_teams
from sqlalchemy import Connection, Engine, text

from presagio.ingestion.loaders.odds import seed_odds_team_aliases
from presagio.ingestion.raw_store import canonical_json, request_hash, response_sha256
from presagio.ingestion.sources.odds.the_odds_api import TEAM_NAME_SEED

ET = ZoneInfo("America/New_York")
FETCHED = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
STAT_COLUMNS = (
    "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "reb", "ast", "stl", "blk", "tov",
    "pf",
)  # fmt: skip
T4_LATE_GAME = "0022300900"  # 2024-01-10 23:00 ET (04:00 UTC 2024-01-11)
T4_NEXT_DAY_GAME = "0022300901"  # 2024-01-11 19:30 ET
CUP_FINAL = "0062300001"
LIVE_UNMATCHED = "0022600001"  # 2026-10-20
LIVE_AVAILABLE = "0022600002"  # 2026-10-21
LIVE_PRED_ONLY = "0022600003"  # 2026-10-22
LIVE_NOT_FINAL = "0022600004"  # 2026-10-23


def et(day: date, hour: int, minute: int = 0) -> datetime:
    """A US Eastern wall time on `day` as an aware UTC timestamp."""
    return datetime.combine(day, time(hour, minute), tzinfo=ET).astimezone(UTC)


def stat_values(pts: int) -> dict[str, int]:
    """Filler box score consistent with pts (pts = 2 fgm + fg3m + ftm, reb = oreb + dreb)."""
    ftm = 10 + pts % 2
    fgm = (pts - 10 - ftm) // 2
    values = dict.fromkeys(STAT_COLUMNS, 10)
    values.update(fgm=fgm, fga=fgm + 40, fg3m=10, fg3a=30, ftm=ftm, fta=ftm + 5)
    values.update(oreb=10, dreb=30, reb=40)
    return values


@dataclass(frozen=True)
class League:
    """What the fixture holds, for tests to refer to."""

    teams: tuple[int, int, int, int]  # A, B, C, D
    games: dict[str, dict[str, Any]]  # game_id -> inserted games row
    names: dict[int, str]  # team_id -> the_odds_api alias

    def ids(self, season: str) -> list[str]:
        """Game ids of a season in start order."""
        rows = [g for g in self.games.values() if g["season"] == season]
        return [g["game_id"] for g in sorted(rows, key=lambda g: g["start_time_utc"])]


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


def game_row(
    game_id: str, season: str, start: datetime, home: int, away: int, n: int, final: bool = True
) -> dict[str, Any]:
    """A games row with deterministic, varied, never tied scores."""
    home_score, away_score = 95 + (n * 7) % 31, 95 + (n * 11) % 29
    if home_score == away_score:
        home_score += 1
    return {
        "game_id": game_id,
        "season": season,
        "start_time_utc": start,
        "status_code": 3 if final else 1,
        "status_text": "Final" if final else "7:30 pm ET",
        "home_team_id": home,
        "away_team_id": away,
        "home_score": home_score if final else None,
        "away_score": away_score if final else None,
        "is_neutral": False,
        "source": "leaguegamelog" if final else "scheduleleaguev2",
    }


def build_games(a: int, b: int, c: int, d: int) -> list[dict[str, Any]]:
    """The schedule described in the module docstring."""
    rows: list[dict[str, Any]] = []

    def season_days(season: str, prefix: str, first: date, days: int, start_n: int) -> None:
        """Two games on every other date; home sides alternate."""
        for k in range(days):
            day = first + timedelta(days=2 * k)
            for i, (x, y, hh, mm) in enumerate(((a, b, 19, 30), (c, d, 20, 0))):
                n = start_n + 2 * k + i
                home, away = (x, y) if k % 2 == 0 else (y, x)
                rows.append(game_row(f"{prefix}{n:05d}", season, et(day, hh, mm), home, away, n))

    season_days("2023-24", "00223", date(2023, 10, 24), 85, 1)
    for row in rows:
        if row["start_time_utc"] == et(date(2024, 1, 10), 20, 0):
            row["start_time_utc"] = et(date(2024, 1, 10), 23, 0)
            row["game_id"] = T4_LATE_GAME
    rows.append(game_row(T4_NEXT_DAY_GAME, "2023-24", et(date(2024, 1, 11), 19, 30), a, c, 901))
    cup = game_row(CUP_FINAL, "2023-24", et(date(2023, 12, 10), 20, 30), a, c, 7)
    rows.append({**cup, "is_neutral": True, "source": "scheduleleaguev2"})
    rows.append(game_row("0052300101", "2023-24", et(date(2024, 4, 16), 19, 0), c, d, 3))
    rows.append(game_row("0042300101", "2023-24", et(date(2024, 4, 20), 20, 0), a, b, 4))
    rows.append(game_row("0042300102", "2023-24", et(date(2024, 4, 22), 20, 0), b, a, 5))
    season_days("2024-25", "00224", date(2024, 10, 22), 85, 1)
    rows.append(game_row("0042400101", "2024-25", et(date(2025, 4, 20), 20), a, c, 6))
    season_days("2025-26", "00225", date(2025, 10, 21), 10, 1)
    live = (
        (LIVE_UNMATCHED, date(2026, 10, 20), a, b, True),
        (LIVE_AVAILABLE, date(2026, 10, 21), c, d, True),
        (LIVE_PRED_ONLY, date(2026, 10, 22), a, c, True),
        (LIVE_NOT_FINAL, date(2026, 10, 23), b, d, False),
    )
    for n, (gid, day, home, away, final) in enumerate(live, 1):
        rows.append(game_row(gid, "2026-27", et(day, 19, 30), home, away, 40 + n, final))
    return rows


def insert_games(conn: Connection, rows: list[dict[str, Any]], raw_id: int) -> None:
    """Insert games rows."""
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


def insert_stats(conn: Connection, game: dict[str, Any], raw_id: int) -> None:
    """Two leaguegamelog rows whose pts equal the score."""
    sides = (
        (game["home_team_id"], game["away_team_id"], True, game["home_score"]),
        (game["away_team_id"], game["home_team_id"], False, game["away_score"]),
    )
    conn.execute(
        text(
            f"""
            INSERT INTO team_game_stats (game_id, team_id, source, opponent_team_id, is_home,
                minutes, pts, {", ".join(STAT_COLUMNS)}, plus_minus, fetched_at, raw_response_id)
            VALUES (:game_id, :team_id, 'leaguegamelog', :opponent, :is_home, 240, :pts,
                {", ".join(":" + c for c in STAT_COLUMNS)}, 0, :fetched_at, :raw_id)
            """
        ),
        [
            {
                "game_id": game["game_id"],
                "team_id": team,
                "opponent": opponent,
                "is_home": is_home,
                "pts": pts,
                **stat_values(pts),
                "fetched_at": FETCHED,
                "raw_id": raw_id,
            }
            for team, opponent, is_home, pts in sides
        ],
    )


def insert_event(
    conn: Connection, game: dict[str, Any], names: dict[int, str], raw_id: int, matched: bool
) -> int:
    """One odds_events row for the game (mapped or left unmatched)."""
    return conn.execute(
        text(
            """
            INSERT INTO odds_events (source, source_event_id, home_team_name, away_team_name,
                commence_time, game_id, match_method, matched_at, fetched_at, raw_response_id)
            VALUES ('the_odds_api', :event, :home, :away, :start, :game_id, :method, :matched,
                :at, :raw)
            RETURNING odds_event_id
            """
        ),
        {
            "event": f"evt-{game['game_id']}",
            "home": names[game["home_team_id"]],
            "away": names[game["away_team_id"]],
            "start": game["start_time_utc"],
            "game_id": game["game_id"] if matched else None,
            "method": "alias+time" if matched else None,
            "matched": FETCHED if matched else None,
            "at": FETCHED,
            "raw": raw_id,
        },
    ).scalar_one()


def insert_snapshot(
    conn: Connection,
    event_id: int,
    commence: datetime,
    captured: datetime,
    book: str,
    market: str,
    side: str,
    price: str,
    raw_id: int,
    point: str | None = None,
) -> int:
    """One odds_snapshots row."""
    return conn.execute(
        text(
            """
            INSERT INTO odds_snapshots (odds_event_id, source, captured_at, capture_kind,
                commence_time, bookmaker_key, bookmaker_last_update, market_key, outcome_name,
                outcome_side, price_decimal, point, fetched_at, raw_response_id)
            VALUES (:event, 'the_odds_api', :captured, 'live', :commence, :book, :captured,
                :market, :side, :side, CAST(:price AS numeric), CAST(:point AS numeric),
                :captured, :raw)
            RETURNING odds_snapshot_id
            """
        ),
        {
            "event": event_id,
            "captured": captured,
            "commence": commence,
            "book": book,
            "market": market,
            "side": side,
            "price": price,
            "point": point,
            "raw": raw_id,
        },
    ).scalar_one()


def load_league(engine: Engine) -> League:
    """Insert the league and commit it."""
    teams = sorted(static_teams.get_teams(), key=lambda t: t["id"])
    a, b, c, d = (t["id"] for t in teams[:4])
    rows = build_games(a, b, c, d)
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
        seed_odds_team_aliases(conn)
        tricodes = {t["id"]: t["abbreviation"] for t in teams}
        names = {team_id: TEAM_NAME_SEED[code] for team_id, code in tricodes.items()}
        raw = insert_raw(conn, "nba_api", "leaguegamelog", {"Season": "all"}, {"rows": []})
        insert_games(conn, rows, raw)
        for row in rows:
            if row["status_code"] == 3 and row["game_id"] != CUP_FINAL:
                insert_stats(conn, row, raw)
        odds_raw = insert_raw(conn, "the_odds_api", "odds", {"markets": "h2h,totals"}, [])
        by_id = {r["game_id"]: r for r in rows}
        insert_event(conn, by_id[LIVE_UNMATCHED], names, odds_raw, matched=False)
        available = by_id[LIVE_AVAILABLE]
        event = insert_event(conn, available, names, odds_raw, matched=True)
        tip = available["start_time_utc"]
        pred_at = et(date(2026, 10, 20), 21, 45)
        close_at = tip - timedelta(minutes=45)
        prices = {
            "draftkings": (("2.10", "1.80"), ("1.90", "2.00")),
            "fanduel": (("2.05", "1.83"), ("1.95", "1.95")),
            "betmgm": (("2.15", "1.75"), ("1.92", "1.98")),
        }
        for book, (pred, close) in prices.items():
            for at, (home, away) in ((pred_at, pred), (close_at, close)):
                insert_snapshot(conn, event, tip, at, book, "h2h", "home", home, odds_raw)
                insert_snapshot(conn, event, tip, at, book, "h2h", "away", away, odds_raw)
        for at, line in ((pred_at, "220.5"), (close_at, "222.5")):
            for side in ("over", "under"):
                insert_snapshot(
                    conn, event, tip, at, "draftkings", "totals", side, "1.91", odds_raw, line
                )
        pred_only = by_id[LIVE_PRED_ONLY]
        event = insert_event(conn, pred_only, names, odds_raw, matched=True)
        tip = pred_only["start_time_utc"]
        at = et(date(2026, 10, 21), 21, 45)
        insert_snapshot(conn, event, tip, at, "draftkings", "h2h", "home", "1.95", odds_raw)
        insert_snapshot(conn, event, tip, at, "draftkings", "h2h", "away", "1.95", odds_raw)
    return League((a, b, c, d), {r["game_id"]: r for r in rows}, names)
