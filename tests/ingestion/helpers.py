"""Test helpers for ingestion: fake clock, in-memory raw store, canned HTTP responses."""

import copy
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from presagio.ingestion.client import RequestPolicy
from presagio.ingestion.raw_store import StoredResponse, request_hash, response_sha256

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "nba_api"
T0 = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
# Defaults from D-024, with jitter 0 so backoff sleeps are exact in assertions.
POLICY = RequestPolicy(
    min_interval=1.0, timeout=10.0, max_retries=3, backoff_base=1.0, backoff_jitter=0.0
)


def fixture_bytes(name: str) -> bytes:
    """Raw bytes of a saved nba_api response under tests/fixtures/nba_api."""
    return (FIXTURES / f"{name}.json").read_bytes()


class FakeClock:
    """Monotonic clock that only moves when sleep() or advance() is called."""

    def __init__(self) -> None:
        """Start at t=0 with no sleeps recorded."""
        self.t = 0.0
        self.sleeps: list[float] = []
        self.wall = T0

    def __call__(self) -> float:
        """Current monotonic time."""
        return self.t

    def advance(self, seconds: float) -> None:
        """Let time pass without sleeping (e.g. response latency)."""
        self.t += seconds
        self.wall += timedelta(seconds=seconds)

    def sleep(self, seconds: float) -> None:
        """Record the sleep and move time forward instantly."""
        self.sleeps.append(seconds)
        self.advance(seconds)

    def now(self) -> datetime:
        """Wall-clock UTC time that follows the monotonic clock."""
        return self.wall


@dataclass
class FakeStore:
    """In-memory stand-in for raw_store.store_response with the same versioning rule."""

    calls: list[dict[str, Any]] = field(default_factory=list)
    counts: dict[tuple[str, str], int] = field(default_factory=dict)

    def __call__(self, **kwargs: Any) -> StoredResponse:
        """Remember the call; same (request, content) bumps the count instead of a new row."""
        self.calls.append(kwargs)
        key = (
            request_hash(kwargs["source"], kwargs["endpoint"], kwargs["params"]),
            response_sha256(kwargs["body"]),
        )
        self.counts[key] = self.counts.get(key, 0) + 1
        return StoredResponse(len(self.calls), key[0], key[1], self.counts[key])


@dataclass
class FakeRun:
    """Minimal RunCounter."""

    run_id: int = 1
    api_calls: int = 0
    retry_count: int = 0

    def add_api_calls(self, calls: int, retries: int) -> None:
        """Accumulate counts like RunTracker."""
        self.api_calls += calls
        self.retry_count += retries


Handler = Callable[[httpx.Request], httpx.Response]


def responses(*items: httpx.Response | Exception, clock: FakeClock | None = None) -> Handler:
    """MockTransport handler returning (or raising) the items in order, recording requests."""
    queue = list(items)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """Pop the next canned response; 0.25 s of latency passes on the fake clock."""
        seen.append(request)
        if clock is not None:
            clock.advance(0.25)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    handler.seen = seen  # type: ignore[attr-defined]
    return handler


def html(status: int, body: str = "<html><body>Access Denied</body></html>") -> httpx.Response:
    """A non-JSON HTML response like an Akamai block page."""
    return httpx.Response(status, content=body.encode(), headers={"Content-Type": "text/html"})


def json_bytes(
    status: int, body: bytes, headers: Mapping[str, str] | None = None
) -> httpx.Response:
    """A response with the given JSON bytes."""
    return httpx.Response(
        status, content=body, headers={"Content-Type": "application/json", **(headers or {})}
    )


def fixture_name(request: httpx.Request) -> str:
    """Fixture file a stats.nba.com request maps to, e.g. 'scheduleleaguev2__2026-27'."""
    endpoint = request.url.path.rsplit("/", 1)[-1]
    params = request.url.params
    if endpoint == "scheduleleaguev2":
        return f"scheduleleaguev2__{params['Season']}"
    if endpoint == "leaguegamelog":
        return f"leaguegamelog__{params['Season']}_{params['SeasonType'].replace(' ', '')}"
    if endpoint == "commonteamyears":
        return f"commonteamyears__{params['LeagueID']}"
    raise AssertionError(f"no fixture route for {endpoint}")


def nba_fixture_handler(overrides: dict[str, bytes] | None = None) -> Handler:
    """MockTransport handler serving tests/fixtures/nba_api by request; overrides win.

    The overrides dict is read on every request, so a test can change a response between
    two loads (e.g. a scheduled game that became final).
    """
    seen: list[httpx.Request] = []
    replaced = overrides if overrides is not None else {}

    def handler(request: httpx.Request) -> httpx.Response:
        """Serve the matching fixture bytes with HTTP 200."""
        seen.append(request)
        name = fixture_name(request)
        body = replaced[name] if name in replaced else fixture_bytes(name)
        return json_bytes(200, body)

    handler.seen = seen  # type: ignore[attr-defined]
    return handler


def fixture_json(name: str) -> Any:
    """Parsed copy of a fixture, for tests that edit a response."""
    return json.loads(fixture_bytes(name))


def schedule_games(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Every game object in a ScheduleLeagueV2 response."""
    return [g for day in data["leagueSchedule"]["gameDates"] for g in day["games"]]


def edit_game(data: dict[str, Any], game_id: str, **changes: Any) -> None:
    """Apply changes to one schedule game; 'home'/'away' dicts update the team objects."""
    game = next(g for g in schedule_games(data) if g["gameId"] == game_id)
    game["homeTeam"].update(changes.pop("home", {}))
    game["awayTeam"].update(changes.pop("away", {}))
    game.update(changes)


# Each case would break a games CHECK (or the time parser) if it reached the database.
BROKEN_GAMES = {
    "one_team_decided": ("0022600006", {"away": {"teamId": 0}}, "only one team"),
    "same_team_twice": ("0022600006", {"away": {"teamId": 1610612755}}, "same team"),
    "final_without_score": ("0022600001", {"home": {"score": None}}, "non-negative score"),
    "final_negative_score": ("0022600001", {"away": {"score": -1}}, "non-negative score"),
    "time_not_iso": ("0022600006", {"gameDateTimeUTC": "10/22/2026 23:00"}, "not an ISO time"),
}


def with_allstar_and_club_games(data: dict[str, Any]) -> dict[str, Any]:
    """2025-26 schedule plus an all-star game and a preseason game against a foreign club.

    Team IDs are the ones the real 2025-26 schedule uses for these (not in teams).
    """
    day = data["leagueSchedule"]["gameDates"][0]["games"]
    allstar = copy.deepcopy(day[0])
    allstar["gameId"] = "0032500001"
    allstar["homeTeam"]["teamId"], allstar["awayTeam"]["teamId"] = 1610616859, 1610616860
    club = copy.deepcopy(day[0])
    club["gameId"] = "0012500099"
    club["awayTeam"]["teamId"] = 15016  # Melbourne United
    day.extend([allstar, club])
    return data
