"""The one gate for external calls: pacing, timeout, retries, status check, then raw storage.

Order for every attempt (CLAUDE.md principle 4, D-024):
  wait min_interval since the previous response ended -> send with a timeout
  -> classify the HTTP status (before anything reads the body) -> store the bytes in raw
  -> 2xx: parse JSON and return | 5xx: back off and retry | 403/429: abort | other: fail.

Both sources go through httpx: The Odds API directly, nba_api by sending the URL, parameters and
headers that an nba_api endpoint object describes (see nba_api_request). nba_api's own
send_api_request is not used: it returns re-decoded text instead of the received bytes, rewrites
some error bodies, and parses JSON before anyone could look at the status code.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
from nba_api.live.nba.library.http import NBALiveHTTP
from nba_api.stats.library.http import NBAStatsHTTP
from sqlalchemy import Engine

from presagio.config import Settings, get_settings
from presagio.ingestion.errors import (
    BlockedError,
    ClientRequestError,
    IngestionError,
    InvalidResponseError,
    RetriesExhaustedError,
    TransientTransportError,
)
from presagio.ingestion.raw_store import StoredResponse, parse_json, store_response

# httpx logs every request URL at INFO, query string included; secrets (apiKey) must not leak.
logging.getLogger("httpx").setLevel(logging.WARNING)

SOURCES = frozenset({"nba_api", "the_odds_api"})
BLOCKED_STATUSES = frozenset({403, 429})
# Parameter names that must go in ApiRequest.secret_params, never in the stored params.
SECRET_PARAM_NAMES = frozenset({"apikey", "api_key", "key", "token", "access_token"})
# httpx cannot decode brotli without an extra package and silently returns the compressed bytes
# for an unknown Content-Encoding, so never advertise br (nba_api's default headers do).
ACCEPT_ENCODING = "gzip, deflate"


@dataclass(frozen=True)
class ApiRequest:
    """What to call. params are stored and hashed; secret_params are only sent."""

    source: str
    endpoint: str
    url: str
    params: Mapping[str, str] = field(default_factory=dict)
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    secret_params: Mapping[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        """Validate the source and normalize params to the strings actually sent."""
        if self.source not in SOURCES:
            raise ValueError(f"unknown source {self.source!r}; expected one of {sorted(SOURCES)}")
        leaked = [name for name in self.params if name.lower() in SECRET_PARAM_NAMES]
        if leaked:
            raise ValueError(f"secret-looking params {leaked} must go in secret_params")
        object.__setattr__(self, "params", normalize_params(self.params))
        object.__setattr__(self, "secret_params", normalize_params(self.secret_params))


def normalize_params(params: Mapping[str, Any]) -> dict[str, str]:
    """Drop None (as requests does) and stringify the rest, so the hash matches the wire."""
    return {str(k): str(v) for k, v in params.items() if v is not None}


@dataclass(frozen=True)
class RawResponse:
    """An HTTP response as received: status, body bytes (after transfer decoding), headers."""

    status: int
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)


class Transport(Protocol):
    """Sends one request; raises TransientTransportError when no HTTP response arrives.

    Any other failure to get a usable response is raised as IngestionError (never retried).
    """

    def __call__(self, request: ApiRequest, timeout: float) -> RawResponse:
        """Send the request once, without retries."""
        ...


class HttpxTransport:
    """Transport over an httpx.Client (pass one with MockTransport in tests)."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        """Use the given client, or a default one that does not follow redirects."""
        self.client = client if client is not None else httpx.Client()

    def __call__(self, request: ApiRequest, timeout: float) -> RawResponse:
        """GET the url with sorted params (secrets merged in) and return the raw response."""
        params = sorted({**request.params, **request.secret_params}.items())
        try:
            response = self.client.get(
                request.url, params=params, headers=dict(request.headers), timeout=timeout
            )
        except httpx.TimeoutException as exc:
            # The message names the endpoint only: exc.request.url would carry secret params.
            raise TransientTransportError(
                f"{request.source}/{request.endpoint}: timeout ({type(exc).__name__})",
                timeout=True,
            ) from None
        except httpx.TransportError as exc:
            raise TransientTransportError(
                f"{request.source}/{request.endpoint}: {type(exc).__name__}", timeout=False
            ) from None
        except (httpx.HTTPError, httpx.InvalidURL, httpx.StreamError, httpx.CookieConflict) as exc:
            # Not transient (D-032 3): e.g. DecodingError for a body that fails its
            # Content-Encoding, TooManyRedirects. No bytes to store; fail the run, never retry.
            raise IngestionError(
                f"{request.source}/{request.endpoint}: {type(exc).__name__}, not retried"
            ) from None
        return RawResponse(response.status_code, response.content, dict(response.headers))

    def close(self) -> None:
        """Close the underlying connection pool."""
        self.client.close()


@dataclass(frozen=True)
class RequestPolicy:
    """Pacing, timeout and retry settings; defaults documented in presagio.config (D-024)."""

    min_interval: float
    timeout: float
    max_retries: int
    backoff_base: float
    backoff_jitter: float

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> RequestPolicy:
        """Build the policy from settings (.env / environment)."""
        s = settings if settings is not None else get_settings()
        return cls(
            min_interval=s.http_min_interval,
            timeout=s.http_timeout,
            max_retries=s.http_max_retries,
            backoff_base=s.http_backoff_base,
            backoff_jitter=s.http_backoff_jitter,
        )

    def backoff(self, retry_no: int, rng: random.Random) -> float:
        """Seconds to wait before retry number retry_no (1-based): base * 2**(n-1) + jitter."""
        return self.backoff_base * 2 ** (retry_no - 1) + rng.uniform(0, self.backoff_jitter)


class RunCounter(Protocol):
    """The part of RunTracker the client needs."""

    run_id: int

    def add_api_calls(self, calls: int, retries: int) -> None:
        """Count calls (retries included) against the run."""
        ...


class RawStore(Protocol):
    """Persists one response; presagio.ingestion.raw_store.store_response is the real one."""

    def __call__(
        self,
        *,
        source: str,
        endpoint: str,
        params: Mapping[str, str],
        http_status: int,
        body: bytes,
        fetched_at: datetime,
        ingestion_run_id: int | None = None,
        parsed: Any = ...,
    ) -> StoredResponse:
        """Store and return where the response landed."""
        ...


def db_raw_store(engine: Engine) -> RawStore:
    """RawStore writing to raw_api_responses through the given engine."""

    def _store(**kwargs: Any) -> StoredResponse:
        """Store one response with its own transaction, so it survives a failing run."""
        return store_response(engine, **kwargs)

    return _store


@dataclass(frozen=True)
class FetchResult:
    """A successful (2xx, JSON) response with its raw row."""

    request: ApiRequest
    http_status: int
    data: Any
    body: bytes
    headers: Mapping[str, str]
    fetched_at: datetime
    stored: StoredResponse
    attempts: int


class IngestionClient:
    """Calls external APIs under one policy and stores every HTTP response in raw."""

    def __init__(
        self,
        store: RawStore,
        transport: Transport | None = None,
        policy: RequestPolicy | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        rng: random.Random | None = None,
    ) -> None:
        """Clock, sleep, now and rng are injectable so tests never really wait."""
        self.store = store
        self.transport = transport if transport is not None else HttpxTransport()
        self.policy = policy if policy is not None else RequestPolicy.from_settings()
        self.clock = clock
        self.sleep = sleep
        self.now = now
        self.rng = rng if rng is not None else random.Random()
        # Monotonic time the last response (or failure) ended, per source host.
        self._last_end: dict[str, float] = {}

    def _wait_turn(self, source: str) -> None:
        """Sleep until min_interval has passed since the previous response from this source."""
        last = self._last_end.get(source)
        if last is not None:
            remaining = self.policy.min_interval - (self.clock() - last)
            if remaining > 0:
                self.sleep(remaining)

    def fetch(self, request: ApiRequest, run: RunCounter | None = None) -> FetchResult:
        """Call once (plus retries for transient errors), store each response, return 2xx JSON."""
        attempts = 0
        last_problem = ""
        try:
            while True:
                attempts += 1
                self._wait_turn(request.source)
                try:
                    response = self.transport(request, self.policy.timeout)
                except TransientTransportError as exc:
                    last_problem = str(exc)
                else:
                    result = self._handle(request, response, run, attempts)
                    if result is not None:
                        return result
                    last_problem = f"HTTP {response.status}"
                finally:
                    self._last_end[request.source] = self.clock()
                if attempts > self.policy.max_retries:
                    raise RetriesExhaustedError(
                        f"{request.source}/{request.endpoint}: gave up after {attempts} attempts"
                        f" (last: {last_problem})"
                    )
                self.sleep(self.policy.backoff(attempts, self.rng))
        finally:
            if run is not None:
                run.add_api_calls(attempts, attempts - 1)

    def _handle(
        self, request: ApiRequest, response: RawResponse, run: RunCounter | None, attempts: int
    ) -> FetchResult | None:
        """Classify by status first; store; return the result, None to retry, or raise."""
        status = response.status
        fetched_at = self.now()
        common = {
            "source": request.source,
            "endpoint": request.endpoint,
            "params": request.params,
            "http_status": status,
            "body": response.body,
            "fetched_at": fetched_at,
            "ingestion_run_id": run.run_id if run is not None else None,
        }
        where = f"{request.source}/{request.endpoint}"
        # The status decides what happens; the body is only stored, never interpreted, unless 2xx.
        if 200 <= status < 300:
            is_json, data = parse_json(response.body)
            if not is_json:
                stored = self.store(**common)
                raise InvalidResponseError(
                    f"{where}: HTTP {status} body is not JSON ({len(response.body)} bytes)",
                    raw_response_id=stored.raw_response_id,
                )
            stored = self.store(**common, parsed=data)
            return FetchResult(
                request, status, data, response.body, response.headers, fetched_at, stored, attempts
            )
        stored = self.store(**common)
        if status in BLOCKED_STATUSES:
            raise BlockedError(
                f"{where}: HTTP {status}, source is blocking or throttling us; stopping",
                http_status=status,
                raw_response_id=stored.raw_response_id,
            )
        if 500 <= status < 600:
            return None
        raise ClientRequestError(
            f"{where}: HTTP {status}, not retried",
            http_status=status,
            raw_response_id=stored.raw_response_id,
        )


def nba_api_request(endpoint: Any) -> ApiRequest:
    """Describe an nba_api endpoint object built with get_request=False as an ApiRequest.

    Uses the library's own URL template, parameters and default headers, so P-014 adapters keep
    nba_api's knowledge of each endpoint while the call itself goes through IngestionClient.
    """
    if hasattr(endpoint, "endpoint_url"):
        # Live endpoints (cdn.nba.com): fixed JSON file, no query parameters.
        name = f"live_{type(endpoint).__name__.lower()}"
        url = NBALiveHTTP.base_url.format(endpoint=endpoint.endpoint_url)
        params: Mapping[str, Any] = {}
        base_headers = NBALiveHTTP.headers
    else:
        name = endpoint.endpoint.lower()
        url = NBAStatsHTTP.base_url.format(endpoint=endpoint.endpoint)
        params = endpoint.parameters
        base_headers = NBAStatsHTTP.headers
    headers = dict(getattr(endpoint, "headers", None) or base_headers)
    headers["Accept-Encoding"] = ACCEPT_ENCODING
    return ApiRequest(source="nba_api", endpoint=name, url=url, params=params, headers=headers)
