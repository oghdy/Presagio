"""Test helpers for odds: fixture JSON, a MockTransport handler for The Odds API, a fake key."""

import copy
import json
from collections.abc import Callable, Iterator
from decimal import ROUND_HALF_UP
from pathlib import Path
from typing import Any

import httpx

from presagio.ingestion.odds_math import decimal_to_american

ODDS_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "odds"
ODDS_FIXTURE = "the_odds_api_nba_odds_synthetic"
EVENTS_FIXTURE = "the_odds_api_nba_events_synthetic"
# Not a real key: long and unusual so a substring search over any output finds every leak.
FAKE_KEY = "p019FAKEoddsKEY0123456789abcdef"
# Event ids in the synthetic fixture.
E_STARTED = "5f0c1e2d3a4b5c6d7e8f9012345678e2"  # LAL-GSW, commence 11:00Z (before T0)
E_PHI_CLE = "5f0c1e2d3a4b5c6d7e8f9012345678e1"  # 23:00Z, same game as 0022600006
E_DEN_LAC = "5f0c1e2d3a4b5c6d7e8f9012345678e3"  # 2026-10-24 02:00Z, not in the schedule
PHI_CLE_GAME = "0022600006"
QUOTA = {"x-requests-remaining": "497", "x-requests-used": "3", "x-requests-last": "3"}


def odds_json(name: str = ODDS_FIXTURE) -> Any:
    """A fresh parsed copy of an odds fixture."""
    return json.loads((ODDS_FIXTURES / f"{name}.json").read_text())


def event_of(data: list[dict[str, Any]], event_id: str) -> dict[str, Any]:
    """One event of a parsed /odds response."""
    return next(e for e in data if e["id"] == event_id)


def outcomes_of(data: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """Every outcome object of a parsed /odds response."""
    for event in data:
        for book in event.get("bookmakers", []):
            for market in book["markets"]:
                yield from market["outcomes"]


def bump_prices(data: list[dict[str, Any]], delta: float) -> list[dict[str, Any]]:
    """A copy with every price moved by delta (a different capture of the same games)."""
    data = copy.deepcopy(data)
    for outcome in outcomes_of(data):
        outcome["price"] = round(outcome["price"] + delta, 4)
    return data


def as_american(data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A copy with prices in American format, as oddsFormat=american would return them."""
    data = copy.deepcopy(data)
    for outcome in outcomes_of(data):
        american = decimal_to_american(str(outcome["price"]))
        outcome["price"] = int(american.to_integral_value(rounding=ROUND_HALF_UP))
    return data


def odds_handler(
    bodies: dict[str, Any], status: int = 200, headers: dict[str, str] | None = None
) -> Callable[[httpx.Request], httpx.Response]:
    """MockTransport handler for /odds and /events; bodies maps endpoint -> JSON (read live)."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """Serve bodies[endpoint] with the quota headers The Odds API sends."""
        seen.append(request)
        endpoint = request.url.path.rsplit("/", 1)[-1]
        body = json.dumps(bodies[endpoint]).encode()
        return httpx.Response(
            status,
            content=body,
            headers={"Content-Type": "application/json", **QUOTA, **(headers or {})},
        )

    handler.seen = seen  # type: ignore[attr-defined]
    return handler
