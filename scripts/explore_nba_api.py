"""One-off exploration of nba_api endpoints for ticket P-010 (research spike, not product code).

Calls a small, fixed set of stats.nba.com / cdn.nba.com endpoints through nba_api, saves each raw
response as JSON and writes a call log (status, latency) so the findings in
docs/research/nba_api.md can be reproduced.

Usage:
    uv run python scripts/explore_nba_api.py                 # all phases, ~30 calls
    uv run python scripts/explore_nba_api.py --phase schedule --delay 1.0
    uv run python scripts/explore_nba_api.py --out data/explore_nba_api

Safety rules (CLAUDE.md principle 4): at least 0.6 s between calls, 10 s timeout, no retries,
and the run stops at the first HTTP 403/429 or after two consecutive timeouts/errors.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import nba_api
import requests
from nba_api.live.nba.endpoints import scoreboard as live_scoreboard
from nba_api.stats.endpoints import (
    boxscoresummaryv3,
    boxscoretraditionalv3,
    commonteamyears,
    leaguegamefinder,
    leaguegamelog,
    scheduleleaguev2,
    scoreboardv3,
)
from nba_api.stats.static import teams as static_teams

MIN_DELAY_S = 0.6
TIMEOUT_S = 10
HISTORIC_SEASONS = ["2023-24", "2024-25", "2025-26"]
CURRENT_SEASON = "2026-27"
PHASES = ["static", "schedule", "gamelog", "boxscore", "daily", "rate", "fixtures"]
FIXTURE_DIR = Path("tests/fixtures/nba_api")

# Box score samples: (season, game ID prefix). IDs are read from saved schedule responses.
BOX_SAMPLES = [("2023-24", "002"), ("2025-26", "004"), (CURRENT_SEASON, "002")]


class StopRun(Exception):
    """Raised when the remote side looks like it is blocking or failing us."""


@dataclass
class Runner:
    """Executes endpoint calls with a fixed delay and records raw responses and latency."""

    out_dir: Path
    delay_s: float
    log: list[dict[str, Any]] = field(default_factory=list)
    consecutive_failures: int = 0
    last_call_at: float = 0.0

    def call(self, name: str, make: Callable[[], Any]) -> dict[str, Any] | None:
        """Run one endpoint call, save its raw JSON body and log status and latency."""
        wait = self.delay_s - (time.monotonic() - self.last_call_at)
        if self.last_call_at and wait > 0:
            time.sleep(wait)
        started = datetime.now(UTC)
        t0 = time.monotonic()
        entry: dict[str, Any] = {"name": name, "started_at_utc": started.isoformat()}
        body: dict[str, Any] | None = None
        try:
            endpoint = make()
            endpoint.timeout = TIMEOUT_S
            endpoint.get_request()
            resp = endpoint.nba_response
            entry["status"] = resp._status_code
            entry["url"] = resp.get_url()
            raw = resp.get_response()
            entry["bytes"] = len(raw)
            body = json.loads(raw)
            entry["datasets"] = _dataset_shapes(endpoint)
            self.consecutive_failures = 0
        except requests.exceptions.Timeout:
            entry["error"] = "timeout"
            self.consecutive_failures += 1
        except Exception as exc:  # noqa: BLE001 - research script records every failure mode
            entry["error"] = f"{type(exc).__name__}: {exc}"[:300]
            self.consecutive_failures += 1
        finally:
            self.last_call_at = time.monotonic()
            entry["elapsed_s"] = round(time.monotonic() - t0, 3)
            self.log.append(entry)
            print(json.dumps(entry, ensure_ascii=False), flush=True)

        if body is not None:
            (self.out_dir / f"{name}.json").write_text(json.dumps(body, ensure_ascii=False))
        if entry.get("status") in (403, 429):
            raise StopRun(f"{name}: HTTP {entry['status']}")
        if self.consecutive_failures >= 2:
            raise StopRun(f"{name}: two consecutive failures")
        return body


def _dataset_shapes(endpoint: Any) -> dict[str, list[int]]:
    """Return {dataset_name: [rows, cols]} for a loaded nba_api endpoint."""
    shapes: dict[str, list[int]] = {}
    for key, value in vars(endpoint).items():
        data = getattr(value, "data", None)
        if isinstance(data, dict) and "headers" in data and "data" in data:
            shapes[key] = [len(data["data"]), len(data["headers"])]
    return shapes


def phase_static(r: Runner) -> None:
    """Dump the bundled static team list (no network) and the CommonTeamYears endpoint."""
    teams = static_teams.get_teams()
    (r.out_dir / "static_teams.json").write_text(json.dumps(teams, ensure_ascii=False))
    print(json.dumps({"name": "static_teams", "rows": len(teams)}))
    r.call("commonteamyears", lambda: commonteamyears.CommonTeamYears(get_request=False))


def phase_schedule(r: Runner) -> None:
    """Fetch the full league schedule for each target season (1 call per season)."""
    for season in [*HISTORIC_SEASONS, CURRENT_SEASON]:
        r.call(
            f"scheduleleaguev2_{season}",
            lambda s=season: scheduleleaguev2.ScheduleLeagueV2(season=s, get_request=False),
        )


def phase_gamelog(r: Runner) -> None:
    """Fetch team-level game logs per season and season type (1 call each)."""
    combos = [(s, t) for s in HISTORIC_SEASONS for t in ("Regular Season", "Playoffs")]
    combos += [("2025-26", "PlayIn"), (CURRENT_SEASON, "Regular Season")]
    for season, season_type in combos:
        tag = season_type.replace(" ", "")
        r.call(
            f"leaguegamelog_{season}_{tag}",
            lambda s=season, t=season_type: leaguegamelog.LeagueGameLog(
                season=s,
                season_type_all_star=t,
                player_or_team_abbreviation="T",
                get_request=False,
            ),
        )
    r.call(
        "leaguegamefinder_2025-26_Playoffs",
        lambda: leaguegamefinder.LeagueGameFinder(
            player_or_team_abbreviation="T",
            season_nullable="2025-26",
            season_type_nullable="Playoffs",
            league_id_nullable="00",
            get_request=False,
        ),
    )


def _first_final_game_id(out_dir: Path, season: str, prefix: str) -> str | None:
    """Pick the first finished game (gameStatus == 3) with a gameId prefix from a schedule."""
    path = out_dir / f"scheduleleaguev2_{season}.json"
    if not path.exists():
        return None
    body = json.loads(path.read_text())
    for date in body["leagueSchedule"]["gameDates"]:
        for game in date["games"]:
            if game["gameStatus"] == 3 and game["gameId"].startswith(prefix):
                return game["gameId"]
    return None


def phase_boxscore(r: Runner) -> None:
    """Fetch a few traditional/summary box scores (1 call per game); needs the schedule phase."""
    game_ids = []
    for season, prefix in BOX_SAMPLES:
        game_id = _first_final_game_id(r.out_dir, season, prefix)
        if game_id is None:
            print(f"skip box score: no finished {prefix} game in saved {season} schedule")
            continue
        game_ids.append(game_id)
        r.call(
            f"boxscoretraditionalv3_{game_id}",
            lambda g=game_id: boxscoretraditionalv3.BoxScoreTraditionalV3(
                game_id=g, get_request=False
            ),
        )
    if not game_ids:
        return
    gid = game_ids[0]
    r.call(
        f"boxscoresummaryv3_{gid}",
        lambda: boxscoresummaryv3.BoxScoreSummaryV3(game_id=gid, get_request=False),
    )


def phase_daily(r: Runner) -> None:
    """Fetch the date-based scoreboards that an incremental daily job would use."""
    today_et = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    r.call(
        f"scoreboardv3_{today_et}",
        lambda: scoreboardv3.ScoreboardV3(game_date=today_et, get_request=False),
    )
    r.call("live_scoreboard_today", lambda: live_scoreboard.ScoreBoard(get_request=False))


def phase_rate(r: Runner, n: int = 10, month: int = 3) -> None:
    """Make n back-to-back light calls (one scoreboard per day of 2026-<month>) for throttling."""
    for day in range(1, n + 1):
        date = f"2026-{month:02d}-{day:02d}"
        r.call(
            f"rate_scoreboardv3_{date}",
            lambda d=date: scoreboardv3.ScoreboardV3(game_date=d, get_request=False),
        )


def _write_fixture(name: str, body: dict[str, Any]) -> None:
    """Write one trimmed response to tests/fixtures/nba_api as indented JSON."""
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    text = json.dumps(body, ensure_ascii=False, indent=1) + "\n"
    (FIXTURE_DIR / f"{name}.json").write_text(text)
    print(f"fixture {name}.json {len(text.encode()) // 1024} KiB")


GamePick = Callable[[dict[str, Any]], bool]


def _prefix(prefix: str, status: int | None = None) -> GamePick:
    """Build a predicate matching a gameId prefix and, optionally, a gameStatus."""
    return lambda g: g["gameId"].startswith(prefix) and status in (None, g["gameStatus"])


def _trim_schedule(body: dict[str, Any], picks: list[GamePick]) -> dict[str, Any]:
    """Keep, in date order, the first not-yet-kept game matching each predicate; drop the rest."""
    sched = body["leagueSchedule"]
    remaining = list(picks)
    dates = []
    for date in sched["gameDates"]:
        kept = []
        for game in date["games"]:
            hit = next((p for p in remaining if p(game)), None)
            if hit is not None:
                remaining.remove(hit)
                kept.append(game)
        if kept:
            dates.append({**date, "games": kept})
    trimmed = {**sched, "gameDates": dates, "weeks": sched["weeks"][:2]}
    return {**body, "leagueSchedule": trimmed}


def _trim_result_set(body: dict[str, Any], n_games: int) -> dict[str, Any]:
    """Keep the rows (both team rows) of the first n_games distinct GAME_IDs in each result set."""
    sets = []
    for rs in body["resultSets"]:
        idx = rs["headers"].index("GAME_ID")
        ids = list(dict.fromkeys(row[idx] for row in rs["rowSet"]))[:n_games]
        sets.append({**rs, "rowSet": [row for row in rs["rowSet"] if row[idx] in ids]})
    return {**body, "resultSets": sets}


def phase_fixtures(r: Runner) -> None:
    """Trim saved raw responses into small test fixtures (no network)."""
    src = r.out_dir

    def load(name: str) -> dict[str, Any]:
        return json.loads((src / f"{name}.json").read_text())

    # Historic: preseason, regular, NBA Cup final, play-in, playoffs (one game each).
    historic = [_prefix(p) for p in ("001", "002", "006", "005", "004")]
    for season in HISTORIC_SEASONS:
        body = _trim_schedule(load(f"scheduleleaguev2_{season}"), historic)
        _write_fixture(f"scheduleleaguev2__{season}", body)
    # Current: two finished regular games, one scheduled, one with TBD teams/time, Cup final.
    current = [_prefix("002", 3), _prefix("002", 3), _prefix("002", 1)]
    current += [lambda g: g["homeTeam"]["teamId"] == 0, _prefix("006")]
    body = _trim_schedule(load(f"scheduleleaguev2_{CURRENT_SEASON}"), current)
    _write_fixture(f"scheduleleaguev2__{CURRENT_SEASON}", body)

    for path in sorted(src.glob("leaguegamelog_*.json")):
        _, season, season_type = path.stem.split("_")
        body = _trim_result_set(load(path.stem), 2)
        _write_fixture(f"leaguegamelog__{season}_{season_type}", body)
    gf = load("leaguegamefinder_2025-26_Playoffs")
    _write_fixture("leaguegamefinder__2025-26_Playoffs", _trim_result_set(gf, 2))

    for path in sorted(src.glob("boxscore*v3_*.json")):
        endpoint, game_id = path.stem.split("_")
        _write_fixture(f"{endpoint}__{game_id}", load(path.stem))
    _write_fixture("commonteamyears__00", load("commonteamyears"))
    for path in sorted(src.glob("scoreboardv3_*.json")):
        _write_fixture(f"scoreboardv3__{path.stem.split('_')[1]}", load(path.stem))
    _write_fixture("scoreboardv3__2026-03-05", load("rate_scoreboardv3_2026-03-05"))
    _write_fixture("live_scoreboard__todaysScoreboard_00", load("live_scoreboard_today"))


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run the requested phases and write call_log.json."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("data/explore_nba_api"))
    parser.add_argument("--delay", type=float, default=1.0, help="seconds between calls")
    parser.add_argument("--phase", choices=PHASES, action="append")
    parser.add_argument("--rate-month", type=int, default=3, help="month (2026) for rate phase")
    args = parser.parse_args(argv)
    if args.delay < MIN_DELAY_S:
        parser.error(f"--delay must be >= {MIN_DELAY_S}")
    args.out.mkdir(parents=True, exist_ok=True)

    runner = Runner(out_dir=args.out, delay_s=args.delay)
    phases = {
        "static": phase_static,
        "schedule": phase_schedule,
        "gamelog": phase_gamelog,
        "boxscore": phase_boxscore,
        "daily": phase_daily,
        "rate": lambda r: phase_rate(r, month=args.rate_month),
        "fixtures": phase_fixtures,
    }
    status = 0
    try:
        for name in args.phase or PHASES:
            phases[name](runner)
    except StopRun as stop:
        print(f"STOPPED: {stop}", file=sys.stderr)
        status = 2
    finally:
        meta = {"nba_api_version": nba_api.__version__, "delay_s": args.delay, "calls": runner.log}
        log_path = args.out / f"call_log_{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
        log_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
