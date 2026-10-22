"""IngestionClient without a database: pacing, timeout, retry rules, status-before-parse."""

import gzip
import logging
import random
import re
from pathlib import Path

import httpx
import pytest
import requests
from nba_api.live.nba.endpoints import scoreboard as live_scoreboard
from nba_api.stats.endpoints import leaguegamelog, scheduleleaguev2
from nba_api.stats.library.http import NBAStatsHTTP

from presagio import config
from presagio.config import Settings
from presagio.ingestion import client as client_module
from presagio.ingestion.client import ApiRequest, RequestPolicy, nba_api_request
from presagio.ingestion.errors import (
    BlockedError,
    ClientRequestError,
    IngestionError,
    InvalidResponseError,
    RetriesExhaustedError,
)

from .helpers import FakeClock, FakeRun, FakeStore, fixture_bytes, html, json_bytes, responses

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEDULE = nba_api_request(scheduleleaguev2.ScheduleLeagueV2(season="2025-26", get_request=False))
ODDS = ApiRequest(
    source="the_odds_api",
    endpoint="v4/sports/basketball_nba/odds",
    url="https://odds.example.test/v4/sports/basketball_nba/odds",
    params={"regions": "us", "markets": "h2h,spreads,totals", "oddsFormat": "decimal"},
    secret_params={"apiKey": "SECRET-KEY-123"},
)


# --- criterion 1: settings and documented defaults -------------------------------------------


def test_request_policy_defaults_match_d024() -> None:
    """Defaults: 1.0 s interval, 10 s timeout, 3 retries with 1/2/4 s backoff (D-024)."""
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://u:p@h/db")
    policy = RequestPolicy.from_settings(settings)
    assert policy == RequestPolicy(
        min_interval=1.0, timeout=10.0, max_retries=3, backoff_base=1.0, backoff_jitter=0.5
    )
    no_jitter = RequestPolicy(1.0, 10.0, 3, 1.0, 0.0)
    assert [no_jitter.backoff(n, random.Random(0)) for n in (1, 2, 3)] == [1.0, 2.0, 4.0]
    for n in (1, 2, 3):
        assert 2 ** (n - 1) <= policy.backoff(n, random.Random(n)) <= 2 ** (n - 1) + 0.5


def test_interval_below_principle_floor_is_rejected() -> None:
    """HTTP_MIN_INTERVAL under 0.6 s (CLAUDE.md principle 4) is a settings error."""
    with pytest.raises(ValueError, match="http_min_interval"):
        Settings(_env_file=None, database_url="x", http_min_interval=0.5)


def test_env_example_documents_every_default() -> None:
    """.env.example lists each request setting with exactly the code default."""
    example = (REPO_ROOT / ".env.example").read_text()
    expected = {
        "HTTP_MIN_INTERVAL": config.DEFAULT_HTTP_MIN_INTERVAL,
        "HTTP_TIMEOUT": config.DEFAULT_HTTP_TIMEOUT,
        "HTTP_MAX_RETRIES": config.DEFAULT_HTTP_MAX_RETRIES,
        "HTTP_BACKOFF_BASE": config.DEFAULT_HTTP_BACKOFF_BASE,
        "HTTP_BACKOFF_JITTER": config.DEFAULT_HTTP_BACKOFF_JITTER,
        "STALE_RUN_HOURS": config.DEFAULT_STALE_RUN_HOURS,
    }
    for key, default in expected.items():
        match = re.search(rf"^{key}=(.+)$", example, re.MULTILINE)
        assert match, f"{key} missing from .env.example"
        assert float(match.group(1)) == default


# --- normal path and pacing ------------------------------------------------------------------


def test_json_response_is_returned_and_stored_once(make_client, clock: FakeClock) -> None:
    """A 200 JSON body is parsed, stored once with its bytes, and sent with timeout/params."""
    body = fixture_bytes("scheduleleaguev2__2025-26")
    handler = responses(json_bytes(200, body), clock=clock)
    store, run = FakeStore(), FakeRun()
    result = make_client(handler, store).fetch(SCHEDULE, run)

    assert result.http_status == 200
    assert result.data["leagueSchedule"]["seasonYear"] == "2025-26"
    assert result.body == body and result.attempts == 1
    assert len(store.calls) == 1 and store.calls[0]["body"] == body
    assert store.calls[0]["params"] == {"LeagueID": "00", "Season": "2025-26"}
    assert (run.api_calls, run.retry_count) == (1, 0)
    sent = handler.seen[0]
    assert (
        str(sent.url) == "https://stats.nba.com/stats/scheduleleaguev2?LeagueID=00&Season=2025-26"
    )
    assert sent.extensions["timeout"] == {
        "connect": 10.0,
        "read": 10.0,
        "write": 10.0,
        "pool": 10.0,
    }
    assert sent.headers["Accept-Encoding"] == "gzip, deflate"
    assert clock.sleeps == []


def test_interval_is_measured_from_the_end_of_the_previous_response(
    make_client, clock: FakeClock
) -> None:
    """The next request waits min_interval minus the time already spent since the last response."""
    body = fixture_bytes("leaguegamelog__2025-26_PlayIn")
    handler = responses(
        json_bytes(200, body), json_bytes(200, body), json_bytes(200, body), clock=clock
    )
    client = make_client(handler)
    client.fetch(SCHEDULE)
    client.fetch(SCHEDULE)  # immediately after: full interval
    clock.advance(0.4)  # caller works for 0.4 s
    client.fetch(SCHEDULE)
    assert clock.sleeps == [pytest.approx(1.0), pytest.approx(0.6)]


def test_gzip_body_is_hashed_after_transfer_decoding(make_client) -> None:
    """Content-Encoding gzip is undone by httpx; the stored bytes are the JSON document."""
    body = fixture_bytes("commonteamyears__00")
    handler = responses(json_bytes(200, gzip.compress(body), {"Content-Encoding": "gzip"}))
    store = FakeStore()
    make_client(handler, store).fetch(SCHEDULE)
    assert store.calls[0]["body"] == body


# --- criterion 2: status before parse, no retry on 4xx ----------------------------------------


def test_html_403_aborts_immediately_without_parsing(make_client, clock, monkeypatch) -> None:
    """HTML 403 (nba_api's block page): no retry, no JSON parse, body still stored."""

    def parse_must_not_run(body: bytes) -> None:
        """Fail the test if the client reads a non-2xx body as JSON."""
        raise AssertionError("parsed a 403 body")

    monkeypatch.setattr(client_module, "parse_json", parse_must_not_run)
    handler = responses(html(403), clock=clock)
    store, run = FakeStore(), FakeRun()
    with pytest.raises(BlockedError) as excinfo:
        make_client(handler, store).fetch(SCHEDULE, run)

    assert excinfo.value.http_status == 403
    assert excinfo.value.raw_response_id == 1
    assert len(handler.seen) == 1 and clock.sleeps == []
    assert (run.api_calls, run.retry_count) == (1, 0)
    assert store.calls[0]["http_status"] == 403
    assert store.calls[0]["body"].startswith(b"<html>")


def test_429_aborts_immediately(make_client, clock) -> None:
    """429 is a throttle signal: one call, no backoff, BlockedError."""
    handler = responses(json_bytes(429, b'{"message": "Usage quota has been reached"}'))
    with pytest.raises(BlockedError) as excinfo:
        make_client(handler).fetch(ODDS)
    assert excinfo.value.http_status == 429
    assert len(handler.seen) == 1 and clock.sleeps == []


@pytest.mark.parametrize("status", [400, 401, 404, 422, 302])
def test_other_non_success_statuses_fail_without_retry(make_client, clock, status) -> None:
    """Other 4xx (and unexpected 3xx) fail at once and are stored."""
    handler = responses(html(status))
    store = FakeStore()
    with pytest.raises(ClientRequestError):
        make_client(handler, store).fetch(SCHEDULE)
    assert len(handler.seen) == 1 and clock.sleeps == [] and len(store.calls) == 1


def test_non_json_200_is_stored_then_rejected(make_client) -> None:
    """A 200 that is not JSON (format change) is kept in raw and raises InvalidResponseError."""
    handler = responses(html(200, "<html>maintenance</html>"))
    store = FakeStore()
    with pytest.raises(InvalidResponseError):
        make_client(handler, store).fetch(SCHEDULE)
    assert len(store.calls) == 1 and len(handler.seen) == 1


# --- transient errors are retried with exponential backoff ------------------------------------


def test_5xx_then_success_counts_the_retry(make_client, clock) -> None:
    """503 then 200: one retry after 1 s backoff; both responses are stored."""
    ok = fixture_bytes("leaguegamelog__2025-26_PlayIn")
    handler = responses(html(503, "<html>busy</html>"), json_bytes(200, ok), clock=clock)
    store, run = FakeStore(), FakeRun()
    result = make_client(handler, store).fetch(SCHEDULE, run)
    assert result.attempts == 2 and result.http_status == 200
    assert (run.api_calls, run.retry_count) == (2, 1)
    assert [c["http_status"] for c in store.calls] == [503, 200]
    assert clock.sleeps == [1.0]


def test_timeout_then_success(make_client, clock) -> None:
    """A read timeout is retried; nothing is stored for the attempt without a response."""
    ok = fixture_bytes("leaguegamelog__2025-26_PlayIn")
    handler = responses(httpx.ReadTimeout("timed out"), json_bytes(200, ok), clock=clock)
    store, run = FakeStore(), FakeRun()
    result = make_client(handler, store).fetch(SCHEDULE, run)
    assert result.attempts == 2
    assert (run.api_calls, run.retry_count) == (2, 1)
    assert [c["http_status"] for c in store.calls] == [200]
    assert clock.sleeps == [1.0]


def test_transient_errors_give_up_after_three_retries(make_client, clock) -> None:
    """Four attempts (1 + 3 retries) with 1, 2, 4 s backoff, then RetriesExhaustedError."""
    handler = responses(
        html(500), httpx.ConnectError("refused"), html(502), httpx.ReadTimeout("slow"), clock=clock
    )
    run = FakeRun()
    with pytest.raises(RetriesExhaustedError, match="4 attempts"):
        make_client(handler).fetch(SCHEDULE, run)
    assert len(handler.seen) == 4
    assert clock.sleeps == [1.0, 2.0, 4.0]
    assert (run.api_calls, run.retry_count) == (4, 3)


@pytest.mark.parametrize(
    ("failure", "name"),
    [
        # A body that does not match its Content-Encoding: httpx raises DecodingError when the
        # client reads it (a stream, so the Response is not decoded while building it here).
        (
            httpx.Response(
                200, stream=httpx.ByteStream(b"not gzip"), headers={"Content-Encoding": "gzip"}
            ),
            "DecodingError",
        ),
        (httpx.TooManyRedirects("redirect loop"), "TooManyRedirects"),
    ],
    ids=["decoding_error", "too_many_redirects"],
)
def test_non_transport_httpx_errors_become_ingestion_errors(
    make_client, clock, failure, name
) -> None:
    """httpx errors outside TransportError are IngestionError, not retried (D-032 follow-up c)."""
    handler = responses(failure, json_bytes(200, b"{}"), clock=clock)
    store, run = FakeStore(), FakeRun()
    with pytest.raises(IngestionError) as caught:
        make_client(handler, store).fetch(ODDS, run)
    assert type(caught.value) is IngestionError  # not TransientTransportError: no retry
    assert f"{name}, not retried" in str(caught.value)
    assert "SECRET-KEY-123" not in str(caught.value) and caught.value.__cause__ is None
    assert len(handler.seen) == 1 and clock.sleeps == []
    assert store.calls == []  # no bytes arrived intact, so nothing to store
    assert (run.api_calls, run.retry_count) == (1, 0)


# --- secrets ----------------------------------------------------------------------------------


def test_secret_params_are_sent_but_never_stored_or_shown(make_client, caplog) -> None:
    """apiKey goes on the wire only: not in stored params, repr, errors or httpx logs."""
    caplog.set_level(logging.DEBUG)
    handler = responses(
        json_bytes(200, b"[]"), httpx.ReadTimeout("t"), html(500), html(500), html(500)
    )
    store = FakeStore()
    client = make_client(handler, store)
    client.fetch(ODDS)
    with pytest.raises(RetriesExhaustedError) as excinfo:
        client.fetch(ODDS)

    assert handler.seen[0].url.params["apiKey"] == "SECRET-KEY-123"
    assert "apiKey" not in store.calls[0]["params"]
    for text in (repr(ODDS), str(excinfo.value), repr(excinfo.value), caplog.text):
        assert "SECRET-KEY-123" not in text


def test_secret_looking_param_must_be_declared_secret() -> None:
    """Putting apiKey in the stored params is a programming error."""
    with pytest.raises(ValueError, match="secret_params"):
        ApiRequest(
            source="the_odds_api", endpoint="x", url="https://x.test", params={"apiKey": "k"}
        )


# --- nba_api requests -------------------------------------------------------------------------


def test_nba_api_request_matches_what_nba_api_would_send() -> None:
    """URL and query equal nba_api's own requests call; headers are nba_api's minus brotli."""
    endpoint = leaguegamelog.LeagueGameLog(
        season="2025-26",
        season_type_all_star="Regular Season",
        player_or_team_abbreviation="T",
        get_request=False,
    )
    request = nba_api_request(endpoint)
    assert request.endpoint == "leaguegamelog"
    sent = httpx.Request("GET", request.url, params=sorted(request.params.items()))
    # nba_api: session.get(url=base_url, params=sorted(parameters.items()), ...)
    theirs = requests.Request(
        "GET",
        NBAStatsHTTP.base_url.format(endpoint="leaguegamelog"),
        params=sorted(endpoint.parameters.items()),
    ).prepare()
    # requests writes a space as '+', httpx as '%20'; both decode to the same parameters.
    assert str(sent.url).replace("%20", "+") == theirs.url
    expected_headers = {**NBAStatsHTTP.headers, "Accept-Encoding": "gzip, deflate"}
    assert dict(request.headers) == expected_headers


def test_nba_api_live_endpoint_request() -> None:
    """Live endpoints become live_<class> on cdn.nba.com with no parameters."""
    request = nba_api_request(live_scoreboard.ScoreBoard(get_request=False))
    assert request.endpoint == "live_scoreboard"
    assert (
        request.url
        == "https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json"
    )
    assert request.params == {}
