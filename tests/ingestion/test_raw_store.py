"""raw_api_responses: same request + same bytes = one row; changed bytes = a new version row."""

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from presagio.db.models import IngestionRun, RawApiResponse
from presagio.ingestion.client import ApiRequest, db_raw_store
from presagio.ingestion.errors import BlockedError
from presagio.ingestion.raw_store import (
    latest_success,
    request_hash,
    response_sha256,
    store_response,
)
from presagio.ingestion.runs import track_run

from .helpers import T0, fixture_bytes, html, json_bytes, responses

PARAMS = {"LeagueID": "00", "Season": "2025-26"}
REQUEST = ApiRequest(
    source="nba_api",
    endpoint="scheduleleaguev2",
    url="https://stats.nba.com/stats/scheduleleaguev2",
    params=PARAMS,
)


def _store(engine: Engine, body: bytes, at=T0, status: int = 200, **kw: Any):
    """Store one scheduleleaguev2 response."""
    return store_response(
        engine,
        source="nba_api",
        endpoint="scheduleleaguev2",
        params=PARAMS,
        http_status=status,
        body=body,
        fetched_at=at,
        **kw,
    )


def _rows(engine: Engine) -> list[RawApiResponse]:
    """All raw rows in insertion order."""
    with Session(engine) as session:
        stmt = select(RawApiResponse).order_by(RawApiResponse.raw_response_id)
        return list(session.scalars(stmt))


def test_request_hash_is_canonical_and_excludes_nothing_but_order() -> None:
    """Key order does not matter; source, endpoint and any value do."""
    base = request_hash("nba_api", "leaguegamelog", {"Season": "2025-26", "LeagueID": "00"})
    assert base == request_hash("nba_api", "leaguegamelog", {"LeagueID": "00", "Season": "2025-26"})
    assert len(base) == 64 and int(base, 16) >= 0
    assert base != request_hash("nba_api", "leaguegamelog", {"LeagueID": "00", "Season": "2024-25"})
    assert base != request_hash(
        "nba_api", "scheduleleaguev2", {"LeagueID": "00", "Season": "2025-26"}
    )
    assert base != request_hash(
        "the_odds_api", "leaguegamelog", {"LeagueID": "00", "Season": "2025-26"}
    )


def test_same_content_twice_updates_the_same_row(engine: Engine) -> None:
    """Criterion 3: identical bytes -> still one row, fetch_count 2, last_fetched_at moves."""
    body = fixture_bytes("scheduleleaguev2__2025-26")
    first = _store(engine, body, at=T0)
    second = _store(engine, body, at=T0 + timedelta(hours=1))

    assert first.is_new_version and not second.is_new_version
    assert first.raw_response_id == second.raw_response_id
    [row] = _rows(engine)
    assert row.fetch_count == 2
    assert row.fetched_at == T0
    assert row.last_fetched_at == T0 + timedelta(hours=1)
    assert row.response_sha256 == response_sha256(body)
    assert row.response_bytes == len(body)
    assert row.request_hash == request_hash("nba_api", "scheduleleaguev2", PARAMS)
    assert row.request_params == PARAMS
    assert row.response_json["leagueSchedule"]["seasonYear"] == "2025-26"
    assert row.response_text is None


def test_changed_content_creates_a_new_version_row(engine: Engine) -> None:
    """Criterion 3: different bytes for the same request -> a second row, same request_hash."""
    _store(engine, fixture_bytes("scheduleleaguev2__2025-26"), at=T0)
    _store(engine, fixture_bytes("scheduleleaguev2__2024-25"), at=T0 + timedelta(days=1))
    rows = _rows(engine)
    assert len(rows) == 2
    assert rows[0].request_hash == rows[1].request_hash
    assert rows[0].response_sha256 != rows[1].response_sha256
    assert [r.fetch_count for r in rows] == [1, 1]


def test_whitespace_only_change_is_a_new_version(engine: Engine) -> None:
    """Versions follow the bytes, not the parsed meaning (D-028 A-1)."""
    _store(engine, b'{"a": 1}')
    _store(engine, b'{"a":1}')
    rows = _rows(engine)
    assert len(rows) == 2 and rows[0].response_json == rows[1].response_json


def test_last_fetched_at_never_moves_backwards(engine: Engine) -> None:
    """Storing an older fetch late keeps the newer last_fetched_at."""
    _store(engine, b"{}", at=T0 + timedelta(hours=2))
    _store(engine, b"{}", at=T0)
    [row] = _rows(engine)
    assert row.last_fetched_at == T0 + timedelta(hours=2) and row.fetch_count == 2


def test_html_body_goes_to_response_text(engine: Engine) -> None:
    """A non-JSON 403 block page is kept verbatim in response_text."""
    body = b"<html><body>Access Denied</body></html>"
    _store(engine, body, status=403)
    [row] = _rows(engine)
    assert row.http_status == 403
    assert row.response_json is None and row.response_text == body.decode()
    assert row.response_sha256 == response_sha256(body)


@pytest.mark.parametrize("body", [b'{"name": "a\\u0000b"}', b'{"x": NaN}'])
def test_json_that_jsonb_rejects_falls_back_to_text(engine: Engine, body: bytes) -> None:
    """Valid-for-Python JSON that jsonb refuses is stored as text, and still deduplicated."""
    _store(engine, body)
    _store(engine, body, at=T0 + timedelta(minutes=5))
    [row] = _rows(engine)
    assert row.response_json is None and row.response_text == body.decode()
    assert row.fetch_count == 2


def test_meta_time_is_kept_verbatim(engine: Engine) -> None:
    """meta.time goes to source_meta_time as text; responses without meta leave it NULL."""
    _store(engine, fixture_bytes("boxscoretraditionalv3__0022300061"))
    _store(engine, fixture_bytes("leaguegamelog__2025-26_PlayIn"))
    with_meta, without_meta = _rows(engine)
    assert with_meta.source_meta_time == "2025-02-17T16:54:39.5439Z"
    assert without_meta.source_meta_time is None


def test_latest_success_skips_error_versions(engine: Engine) -> None:
    """Q10 helper returns the newest 2xx content, not a later 503 page."""
    _store(engine, b'{"v": 1}', at=T0)
    _store(engine, b"<html>busy</html>", at=T0 + timedelta(hours=1), status=503)
    latest = latest_success(engine, request_hash("nba_api", "scheduleleaguev2", PARAMS))
    assert latest is not None and latest.response_json == {"v": 1}


# --- through the client, with a run ------------------------------------------------------------


def test_client_html_403_is_stored_as_text_and_aborts_the_run(engine, make_client) -> None:
    """End to end: HTML 403 -> one call, raw text row linked to the run, run 'aborted'."""
    handler = responses(html(403))
    client = make_client(handler, db_raw_store(engine))
    with (
        pytest.raises(BlockedError),
        track_run(engine, "schedule", "season=2025-26", source="nba_api") as run,
    ):
        client.fetch(REQUEST, run)

    [raw] = _rows(engine)
    assert raw.http_status == 403 and raw.response_text.startswith("<html>")
    assert raw.ingestion_run_id == run.run_id
    with Session(engine) as session:
        row = session.get(IngestionRun, run.run_id)
    assert row.status == "aborted"
    assert (row.api_calls, row.retry_count) == (1, 0)
    assert row.error_message.startswith("BlockedError: nba_api/scheduleleaguev2: HTTP 403")


def test_client_retry_then_success_records_both_versions(engine, make_client) -> None:
    """503 then 200: two raw rows (error page and data), run succeeded with 2 calls, 1 retry."""
    ok = fixture_bytes("scheduleleaguev2__2025-26")
    handler = responses(html(503, "<html>busy</html>"), json_bytes(200, ok))
    client = make_client(handler, db_raw_store(engine))
    with track_run(engine, "schedule", "season=2025-26", source="nba_api") as run:
        result = client.fetch(REQUEST, run)
        run.item_succeeded()

    assert [r.http_status for r in _rows(engine)] == [503, 200]
    assert result.stored.raw_response_id == _rows(engine)[1].raw_response_id
    with Session(engine) as session:
        row = session.get(IngestionRun, run.run_id)
    assert row.status == "succeeded"
    assert (row.api_calls, row.retry_count, row.items_succeeded) == (2, 1, 1)


def test_client_same_request_twice_keeps_one_row(engine, make_client) -> None:
    """Fetching identical content twice through the client leaves one row with fetch_count 2."""
    ok = fixture_bytes("scheduleleaguev2__2025-26")
    client = make_client(responses(json_bytes(200, ok), json_bytes(200, ok)), db_raw_store(engine))
    first = client.fetch(REQUEST)
    second = client.fetch(REQUEST)
    assert first.stored.is_new_version and not second.stored.is_new_version
    [row] = _rows(engine)
    assert row.fetch_count == 2 and row.last_fetched_at > row.fetched_at
