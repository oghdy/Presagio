"""raw_api_responses: keep every response verbatim, one row per distinct content (schema ch. 3)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DataError
from sqlalchemy.orm import Session

from presagio.db.models import RawApiResponse

RAW = RawApiResponse.__table__

# Marks "body not parsed yet" (None is a valid JSON value).
_UNPARSED: Any = object()


@dataclass(frozen=True)
class StoredResponse:
    """Where a response landed: its row, and whether that row is new content."""

    raw_response_id: int
    request_hash: str
    response_sha256: str
    fetch_count: int

    @property
    def is_new_version(self) -> bool:
        """True when this content was seen for the first time for this request."""
        return self.fetch_count == 1


def canonical_json(value: Any) -> str:
    """Serialize with sorted keys and no whitespace, so equal values give equal strings."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def request_hash(source: str, endpoint: str, params: Mapping[str, str]) -> str:
    """sha256 hex of the canonical {source, endpoint, request_params}; params exclude secrets."""
    payload = {"source": source, "endpoint": endpoint, "request_params": dict(params)}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def response_sha256(body: bytes) -> str:
    """sha256 hex of the response bytes exactly as received (before any parsing)."""
    return hashlib.sha256(body).hexdigest()


def parse_json(body: bytes) -> tuple[bool, Any]:
    """Return (True, value) if the bytes are JSON, else (False, None); never raises."""
    try:
        return True, json.loads(body)
    except (ValueError, RecursionError):
        return False, None


def body_as_text(body: bytes) -> str:
    """Decode a non-JSON body for the text column; undecodable bytes and NUL become U+FFFD."""
    # PostgreSQL text cannot hold NUL. response_sha256 still records the exact bytes.
    return body.decode("utf-8", errors="replace").replace("\x00", "�")


def source_meta_time(value: Any) -> str | None:
    """The response's meta.time string verbatim (when the source generated it), if present."""
    if isinstance(value, dict):
        meta = value.get("meta")
        if isinstance(meta, dict) and isinstance(meta.get("time"), str):
            return meta["time"]
    return None


def store_response(
    engine: Engine,
    *,
    source: str,
    endpoint: str,
    params: Mapping[str, str],
    http_status: int,
    body: bytes,
    fetched_at: datetime,
    ingestion_run_id: int | None = None,
    parsed: Any = _UNPARSED,
) -> StoredResponse:
    """Insert a new content version, or bump last_fetched_at/fetch_count of identical content."""
    if parsed is _UNPARSED:
        is_json, parsed = parse_json(body)
    else:
        is_json = True
    req_hash = request_hash(source, endpoint, params)
    content_hash = response_sha256(body)
    row = {
        "source": source,
        "endpoint": endpoint,
        "request_params": dict(params),
        "request_hash": req_hash,
        "http_status": http_status,
        "response_sha256": content_hash,
        "response_bytes": len(body),
        "source_meta_time": source_meta_time(parsed) if is_json else None,
        "fetched_at": fetched_at,
        "last_fetched_at": fetched_at,
        "ingestion_run_id": ingestion_run_id,
    }
    with engine.begin() as conn:
        if is_json:
            try:
                # Savepoint: jsonb rejects some valid JSON (\u0000 in a string, NaN), and then
                # the body is kept as text instead of being dropped (schema ch. 3).
                with conn.begin_nested():
                    result = conn.execute(_upsert({**row, "response_json": parsed}))
            except DataError:
                result = conn.execute(_upsert({**row, "response_text": body_as_text(body)}))
        else:
            result = conn.execute(_upsert({**row, "response_text": body_as_text(body)}))
        raw_id, fetch_count = result.one()
    return StoredResponse(raw_id, req_hash, content_hash, fetch_count)


def _upsert(row: dict[str, Any]) -> Any:
    """INSERT ... ON CONFLICT (request_hash, response_sha256) DO UPDATE the fetch bookkeeping."""
    stmt = insert(RAW).values(**row)
    excluded = stmt.excluded
    return stmt.on_conflict_do_update(
        constraint="uq_raw_request_content",
        set_={
            # Never move backwards if an older fetch is stored late.
            "last_fetched_at": func.greatest(RAW.c.last_fetched_at, excluded.last_fetched_at),
            "fetch_count": RAW.c.fetch_count + 1,
        },
    ).returning(RAW.c.raw_response_id, RAW.c.fetch_count)


def latest_success(engine: Engine, req_hash: str) -> RawApiResponse | None:
    """Most recently fetched 2xx content for a request (query Q10), or None."""
    stmt = (
        select(RawApiResponse)
        .where(RawApiResponse.request_hash == req_hash)
        .where(RawApiResponse.http_status.between(200, 299))
        .order_by(RawApiResponse.last_fetched_at.desc())
        .limit(1)
    )
    with Session(engine) as session:
        return session.scalars(stmt).first()
