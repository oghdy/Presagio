"""Exceptions raised by the ingestion layer; none of their messages carry secrets or URLs."""

from __future__ import annotations


class IngestionError(Exception):
    """Base class for every ingestion failure."""


class HttpStatusError(IngestionError):
    """A response arrived with a status that is not a success; the body is already in raw."""

    def __init__(
        self, message: str, *, http_status: int, raw_response_id: int | None = None
    ) -> None:
        """Keep the status and the raw row id so callers can inspect the stored body."""
        super().__init__(message)
        self.http_status = http_status
        self.raw_response_id = raw_response_id


class BlockedError(HttpStatusError):
    """HTTP 403 or 429: the source refuses or throttles us. Never retried; stop the run."""


class ClientRequestError(HttpStatusError):
    """Any other non-retryable status (4xx except 403/429, or an unexpected 1xx/3xx)."""


class RetriesExhaustedError(IngestionError):
    """Transient errors (timeout, connection error, 5xx) persisted through every retry."""


class InvalidResponseError(IngestionError):
    """A 2xx response whose body is not JSON (format change, captive page); stored as text."""

    def __init__(self, message: str, *, raw_response_id: int | None = None) -> None:
        """Keep the raw row id of the stored body."""
        super().__init__(message)
        self.raw_response_id = raw_response_id


class TransientTransportError(IngestionError):
    """Raised by a transport for a timeout or connection error (no HTTP response)."""

    def __init__(self, message: str, *, timeout: bool) -> None:
        """Remember whether this was a timeout (vs. a connection failure)."""
        super().__init__(message)
        self.timeout = timeout


class RunAlreadyRunningError(IngestionError):
    """Another run of the same job_type and scope_key is still 'running'."""
