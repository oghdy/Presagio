"""Tests for settings loading and the database connection helpers."""

import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import URL, Engine, make_url, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from presagio.config import Settings
from presagio.db import session as db_session
from presagio.db.session import check_connection, make_engine


def test_settings_read_database_url_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """DATABASE_URL and DB_CONNECT_TIMEOUT are read from the environment."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db.example:6543/d")
    monkeypatch.setenv("DB_CONNECT_TIMEOUT", "7")
    settings = Settings(_env_file=None)
    assert settings.database_url == "postgresql+psycopg://u:p@db.example:6543/d"
    assert settings.db_connect_timeout == 7


def test_settings_require_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without DATABASE_URL, settings fail loudly instead of guessing a default."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_check_connection_fails_fast_when_unreachable() -> None:
    """A closed local port raises OperationalError well within the timeout."""
    engine = make_engine("postgresql+psycopg://presagio:presagio@127.0.0.1:1/presagio", 1)
    start = time.monotonic()
    with pytest.raises(OperationalError):
        check_connection(engine)
    assert time.monotonic() - start < 5
    engine.dispose()


def test_make_engine_with_explicit_url_ignores_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Passing a url alone must not read settings, so it works without a .env file."""

    def _no_settings() -> Settings:
        raise AssertionError("make_engine(url) must not load settings")

    monkeypatch.setattr(db_session, "get_settings", _no_settings)
    engine = make_engine("postgresql+psycopg://presagio:presagio@127.0.0.1:1/presagio")
    assert engine.url.port == 1
    engine.dispose()


def test_nothing_listening_skips(
    monkeypatch: pytest.MonkeyPatch, connect_test_engine: Callable[[URL], Engine]
) -> None:
    """A closed port means "no database here", which skips DB tests."""
    monkeypatch.delenv("PRESAGIO_REQUIRE_DB", raising=False)
    with pytest.raises(pytest.skip.Exception, match="no database listening at 127.0.0.1:1"):
        connect_test_engine(make_url("postgresql+psycopg://presagio:presagio@127.0.0.1:1/presagio"))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("password", "definitely-wrong", "password authentication failed"),
        ("database", "presagio_missing", 'database "presagio_missing" does not exist'),
    ],
)
def test_wrong_settings_fail_instead_of_skip(
    db_engine: Engine,
    connect_test_engine: Callable[[URL], Engine],
    field: str,
    value: str,
    message: str,
) -> None:
    """A listening server that rejects the settings fails the test with the real cause."""
    bad_url = db_engine.url.set(**{field: value})
    with pytest.raises(pytest.fail.Exception, match=message) as excinfo:
        connect_test_engine(bad_url)
    if field == "password":
        # The real fake password equals the user name ("presagio"), so check the distinctive one.
        assert value not in str(excinfo.value)


@pytest.mark.parametrize("extra_args", [[], ["--showlocals"]], ids=["default", "showlocals"])
def test_failing_db_run_never_prints_password(db_engine: Engine, extra_args: list[str]) -> None:
    """A real pytest run with a wrong DATABASE_URL password reports the cause, not the password."""
    secret = "presagio-wrong-pw-must-not-leak"
    env = {**os.environ}
    env["DATABASE_URL"] = db_engine.url.set(password=secret).render_as_string(hide_password=False)
    env.pop("PRESAGIO_REQUIRE_DB", None)
    target = f"{Path(__file__).resolve()}::test_session_runs_query"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *extra_args, target],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "password authentication failed" in output
    assert secret not in output


def test_check_connection_returns_server_version(db_engine: Engine) -> None:
    """check_connection reaches the configured PostgreSQL and reports its version."""
    version = check_connection(db_engine)
    assert version.startswith("PostgreSQL")


def test_session_runs_query(db_engine: Engine) -> None:
    """A Session bound to the engine can run a trivial query."""
    with Session(db_engine) as session:
        assert session.execute(text("SELECT 1")).scalar_one() == 1
