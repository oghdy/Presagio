"""Settings: the P-043 odds rejected-price ratio (default, environment, bounds)."""

import pytest
from pydantic import ValidationError

from presagio.config import DEFAULT_ODDS_REJECTED_PRICE_MAX_RATIO, Settings

DB_URL = "postgresql+psycopg://user:not-a-secret@127.0.0.1:5432/presagio"


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """A minimal environment: a database URL and no ratio set."""
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    monkeypatch.delenv("ODDS_REJECTED_PRICE_MAX_RATIO", raising=False)
    return monkeypatch


def test_rejected_price_ratio_defaults_to_two_percent(env: pytest.MonkeyPatch) -> None:
    """ODDS_REJECTED_PRICE_MAX_RATIO defaults to 0.02 (P-043)."""
    assert DEFAULT_ODDS_REJECTED_PRICE_MAX_RATIO == 0.02
    settings = Settings(_env_file=None)
    assert settings.odds_rejected_price_max_ratio == DEFAULT_ODDS_REJECTED_PRICE_MAX_RATIO


@pytest.mark.parametrize(("value", "expected"), [("0", 0.0), ("0.05", 0.05), ("1", 1.0)])
def test_rejected_price_ratio_from_the_environment(
    env: pytest.MonkeyPatch, value: str, expected: float
) -> None:
    """The environment variable sets the ratio; 0 and 1 are both allowed."""
    env.setenv("ODDS_REJECTED_PRICE_MAX_RATIO", value)
    assert Settings(_env_file=None).odds_rejected_price_max_ratio == expected


@pytest.mark.parametrize("value", ["-0.1", "1.5", "two"])
def test_rejected_price_ratio_outside_0_to_1_is_refused(
    env: pytest.MonkeyPatch, value: str
) -> None:
    """A ratio must be a number in [0, 1]."""
    env.setenv("ODDS_REJECTED_PRICE_MAX_RATIO", value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
