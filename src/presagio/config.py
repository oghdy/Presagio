"""Application settings loaded from environment variables and the local .env file."""

import re
from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
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

# The Odds API (D-026, docs/research/odds_api.md). One /odds request costs markets x regions
# credits (3 with the defaults below); the free plan has 500 credits a month.
DEFAULT_ODDS_API_BASE_URL = "https://api.the-odds-api.com/v4"
DEFAULT_ODDS_API_REGIONS = "us"
DEFAULT_ODDS_API_MARKETS = "h2h,spreads,totals"

# Daily ingest times (D-027, P-017 decision proposal). Only scheduling (stage 6) acts on them;
# `presagio ingest-daily --dry-run` prints what they mean for a given date.
# The full run (results, schedule, the "prediction" odds capture), Korean wall time. 16:00 KST
# is 03:00 ET (EDT) / 02:00 ET (EST): every game of the previous ET date has ended.
DEFAULT_INGEST_DAILY_TIME_KST = "16:00"
# `ingest-daily --odds-only` closing captures, US Eastern wall time on the game date, 15 min
# before the usual tip-off slots (19:00/19:30, 20:00/20:30/21:00, 22:00/22:30 ET).
DEFAULT_ODDS_CLOSING_TIMES_ET = "18:45,19:45,21:45"
# D-027: at most four odds captures a day, one of them the full run's.
MAX_ODDS_CLOSING_TIMES = 3
CLOCK_TIME = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")


def parse_clock_times(value: str) -> tuple[str, ...]:
    """'18:45, 19:45' -> ('18:45', '19:45'): HH:MM 24-hour times, sorted, no duplicates."""
    times = [part.strip() for part in value.split(",") if part.strip()]
    bad = [t for t in times if not CLOCK_TIME.match(t)]
    if bad:
        raise ValueError(f"not HH:MM (24-hour) times: {bad}")
    if len(set(times)) != len(times):
        raise ValueError(f"duplicate times in {value!r}")
    return tuple(sorted(times))


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
    odds_api_key: SecretStr | None = Field(
        default=None,
        description="The Odds API key; empty or unset disables the odds adapter. Never logged.",
    )
    odds_api_base_url: str = Field(
        default=DEFAULT_ODDS_API_BASE_URL, description="The Odds API base URL, up to /v4."
    )
    odds_api_regions: str = Field(
        default=DEFAULT_ODDS_API_REGIONS, description="Bookmaker regions, comma separated."
    )
    odds_api_markets: str = Field(
        default=DEFAULT_ODDS_API_MARKETS, description="Markets to request, comma separated."
    )

    ingest_daily_time_kst: str = Field(
        default=DEFAULT_INGEST_DAILY_TIME_KST,
        description="When the full `presagio ingest-daily` runs, HH:MM Korean time (stage 6).",
    )
    odds_closing_times_et: str = Field(
        default=DEFAULT_ODDS_CLOSING_TIMES_ET,
        description="When `ingest-daily --odds-only` runs, HH:MM US Eastern, comma separated.",
    )

    @field_validator("ingest_daily_time_kst")
    @classmethod
    def _one_clock_time(cls, value: str) -> str:
        """Exactly one HH:MM time."""
        times = parse_clock_times(value)
        if len(times) != 1:
            raise ValueError("expected one HH:MM time")
        return times[0]

    @field_validator("odds_closing_times_et")
    @classmethod
    def _closing_times(cls, value: str) -> str:
        """Up to MAX_ODDS_CLOSING_TIMES HH:MM times (D-027: four captures a day in all)."""
        times = parse_clock_times(value)
        if len(times) > MAX_ODDS_CLOSING_TIMES:
            raise ValueError(
                f"at most {MAX_ODDS_CLOSING_TIMES} closing times (D-027: 4 captures a day)"
            )
        return ",".join(times)

    @field_validator("odds_api_key", mode="before")
    @classmethod
    def _blank_key_is_none(cls, value: object) -> object:
        """Treat ODDS_API_KEY= (empty, as in .env.example) as no key."""
        if isinstance(value, str) and not value.strip():
            return None
        return value


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, read once on first use."""
    return Settings()
