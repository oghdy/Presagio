"""Ingestion bookkeeping tables: ingestion_runs and raw_api_responses (schema v1 ch. 3-4)."""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from presagio.db.base import Base, nonnegative

SEASON_PATTERN = "^[0-9]{4}-[0-9]{2}$"
SHA256_PATTERN = "^[0-9a-f]{64}$"


class IngestionRun(Base):
    """One collection job run; a CLI invocation is a parent row, each work unit a child."""

    __tablename__ = "ingestion_runs"

    run_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    parent_run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ingestion_runs.run_id")
    )
    job_type: Mapped[str]
    source: Mapped[str | None]
    scope_key: Mapped[str]
    season: Mapped[str | None]
    season_type: Mapped[str | None]
    target_date_et: Mapped[date | None]
    status: Mapped[str]
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[datetime | None]
    api_calls: Mapped[int] = mapped_column(server_default=text("0"))
    retry_count: Mapped[int] = mapped_column(server_default=text("0"))
    items_total: Mapped[int | None]
    items_succeeded: Mapped[int] = mapped_column(server_default=text("0"))
    items_failed: Mapped[int] = mapped_column(server_default=text("0"))
    rows_written: Mapped[int] = mapped_column(server_default=text("0"))
    error_message: Mapped[str | None]
    error_detail: Mapped[dict[str, Any] | None]
    code_version: Mapped[str | None]

    __table_args__ = (
        CheckConstraint(f"season ~ '{SEASON_PATTERN}'", name="season_format"),
        CheckConstraint(
            "status IN ('running', 'succeeded', 'partial', 'failed', 'aborted')",
            name="status_valid",
        ),
        *(
            nonnegative(column)
            for column in (
                "api_calls",
                "retry_count",
                "items_total",
                "items_succeeded",
                "items_failed",
                "rows_written",
            )
        ),
        CheckConstraint("(status = 'running') = (finished_at IS NULL)", name="finished_iff_done"),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at", name="finished_after_started"
        ),
        # At most one running row per work unit, so the same unit never runs twice at once.
        Index(
            "uq_ingestion_runs_one_running",
            "job_type",
            "scope_key",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
        # Resume lookup: which work units already succeeded (query Q9).
        Index(
            "ix_ingestion_runs_resume",
            "job_type",
            "scope_key",
            postgresql_where=text("status = 'succeeded'"),
        ),
        Index("ix_ingestion_runs_parent", "parent_run_id"),
    )


# Recent runs first (query Q11). DESC indexes need the mapped column, so they live outside.
Index("ix_ingestion_runs_started", IngestionRun.started_at.desc())


class RawApiResponse(Base):
    """An external API response kept verbatim; a new row only when the content changes."""

    __tablename__ = "raw_api_responses"

    raw_response_id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    source: Mapped[str]
    endpoint: Mapped[str]
    request_params: Mapped[dict[str, Any]]
    request_hash: Mapped[str]
    http_status: Mapped[int]
    # Any JSON value (object, array, scalar), so not typed as a dict.
    response_json: Mapped[Any | None] = mapped_column(JSONB)
    response_text: Mapped[str | None]
    response_sha256: Mapped[str]
    response_bytes: Mapped[int]
    source_meta_time: Mapped[str | None]
    fetched_at: Mapped[datetime]
    last_fetched_at: Mapped[datetime]
    fetch_count: Mapped[int] = mapped_column(server_default=text("1"))
    ingestion_run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ingestion_runs.run_id")
    )

    __table_args__ = (
        CheckConstraint("source IN ('nba_api', 'the_odds_api')", name="source_valid"),
        CheckConstraint(f"request_hash ~ '{SHA256_PATTERN}'", name="request_hash_format"),
        CheckConstraint("http_status BETWEEN 100 AND 599", name="http_status_range"),
        CheckConstraint(f"response_sha256 ~ '{SHA256_PATTERN}'", name="response_sha256_format"),
        nonnegative("response_bytes"),
        CheckConstraint("fetch_count >= 1", name="fetch_count_positive"),
        # Same request + same content bytes = same row (idempotency, schema v1 ch. 3).
        UniqueConstraint("request_hash", "response_sha256", name="uq_raw_request_content"),
        CheckConstraint("num_nonnulls(response_json, response_text) = 1", name="exactly_one_body"),
        CheckConstraint("last_fetched_at >= fetched_at", name="last_fetched_after_first"),
        Index("ix_raw_run", "ingestion_run_id"),
    )


# Latest content for a request (query Q10) and operational listing by endpoint.
Index("ix_raw_latest", RawApiResponse.request_hash, RawApiResponse.last_fetched_at.desc())
Index(
    "ix_raw_source_endpoint",
    RawApiResponse.source,
    RawApiResponse.endpoint,
    RawApiResponse.fetched_at.desc(),
)
