"""Ingestion layer: every external call goes through IngestionClient (P-013)."""

from presagio.ingestion.client import (
    ApiRequest,
    FetchResult,
    HttpxTransport,
    IngestionClient,
    RawResponse,
    RequestPolicy,
    db_raw_store,
    nba_api_request,
)
from presagio.ingestion.errors import (
    BlockedError,
    ClientRequestError,
    HttpStatusError,
    IngestionError,
    InvalidResponseError,
    RetriesExhaustedError,
    RunAlreadyRunningError,
    TransientTransportError,
)
from presagio.ingestion.raw_store import StoredResponse, request_hash, store_response
from presagio.ingestion.runs import RunTracker, abort_stale_runs, has_succeeded, track_run

__all__ = [
    "ApiRequest",
    "BlockedError",
    "ClientRequestError",
    "FetchResult",
    "HttpStatusError",
    "HttpxTransport",
    "IngestionClient",
    "IngestionError",
    "InvalidResponseError",
    "RawResponse",
    "RequestPolicy",
    "RetriesExhaustedError",
    "RunAlreadyRunningError",
    "RunTracker",
    "StoredResponse",
    "TransientTransportError",
    "abort_stale_runs",
    "db_raw_store",
    "has_succeeded",
    "nba_api_request",
    "request_hash",
    "store_response",
    "track_run",
]
