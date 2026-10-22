"""Application settings loaded from environment variables and the local .env file."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Seconds to wait for a TCP connection to the database before giving up.
DEFAULT_CONNECT_TIMEOUT = 3

# External request defaults (D-024, docs/research/nba_api.md ch. 4). Used by presagio.ingestion.
# Pause between the end of one response and the start of the next request (s).
DEFAULT_HTTP_MIN_INTERVAL = 1.0
# Lowest interval the code accepts: CLAUDE.md principle 4 / risks B1 floor.
MIN_HTTP_MIN_INTERVAL = 0.6
# Per-request timeout (s); nba_api's own default is 30 s, so it is always passed explicitly.
DEFAULT_HTTP_TIMEOUT = 10.0
# Retries after the first attempt, only for transient errors (timeout, connection error, 5xx).
DEFAULT_HTTP_MAX_RETRIES = 3
# Backoff before retry n is base * 2**(n-1) (1 s, 2 s, 4 s) plus a random jitter in [0, jitter].
DEFAULT_HTTP_BACKOFF_BASE = 1.0
DEFAULT_HTTP_BACKOFF_JITTER = 0.5
# A run left 'running' for longer than this (process died) is closed as 'aborted' (schema ch. 4).
DEFAULT_STALE_RUN_HOURS = 6.0


class Settings(BaseSettings):
    """Runtime settings; real environment variables take precedence over .env."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(
        description="SQLAlchemy URL, e.g. postgresql+psycopg://user:pass@host:port/db",
    )
    db_connect_timeout: int = Field(
        default=DEFAULT_CONNECT_TIMEOUT, ge=1, description="TCP connect timeout (s)."
    )
    http_min_interval: float = Field(
        default=DEFAULT_HTTP_MIN_INTERVAL,
        ge=MIN_HTTP_MIN_INTERVAL,
        description="Minimum pause between the end of a response and the next request (s).",
    )
    http_timeout: float = Field(
        default=DEFAULT_HTTP_TIMEOUT, gt=0, description="Timeout for one external request (s)."
    )
    http_max_retries: int = Field(
        default=DEFAULT_HTTP_MAX_RETRIES,
        ge=0,
        le=10,
        description="Retries after the first attempt, for timeouts, connection errors and 5xx.",
    )
    http_backoff_base: float = Field(
        default=DEFAULT_HTTP_BACKOFF_BASE,
        ge=0,
        description="Backoff before retry n is base * 2**(n-1) seconds.",
    )
    http_backoff_jitter: float = Field(
        default=DEFAULT_HTTP_BACKOFF_JITTER,
        ge=0,
        description="Upper bound of the random seconds added to each backoff.",
    )
    stale_run_hours: float = Field(
        default=DEFAULT_STALE_RUN_HOURS,
        gt=0,
        description="Hours after which a leftover 'running' ingestion run is marked 'aborted'.",
    )


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, read once on first use."""
    return Settings()
