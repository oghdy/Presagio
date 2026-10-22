"""Test helpers for ingestion: fake clock, in-memory raw store, canned HTTP responses."""

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
