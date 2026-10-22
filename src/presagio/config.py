"""Application settings loaded from environment variables and the local .env file."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Seconds to wait for a TCP connection to the database before giving up.
DEFAULT_CONNECT_TIMEOUT = 3


class Settings(BaseSettings):
    """Runtime settings; real environment variables take precedence over .env."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(
        description="SQLAlchemy URL, e.g. postgresql+psycopg://user:pass@host:port/db",
    )
    db_connect_timeout: int = Field(
        default=DEFAULT_CONNECT_TIMEOUT, ge=1, description="TCP connect timeout (s)."
    )


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, read once on first use."""
    return Settings()
