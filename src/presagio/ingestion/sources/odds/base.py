"""Source-neutral odds records and the OddsSource interface (schema v1 ch. 7).

An odds adapter turns one provider's response into an OddsBatch: the events it listed and,
per event, one OddsOutcomeRecord per (bookmaker, market, side) in decimal odds. The loader
writes batches to odds_events / odds_snapshots and never sees provider JSON, so another
provider only needs a new adapter that returns the same records.

"Prediction-time" and "closing" odds are not flags on a record: every batch is one capture
at captured_at, and the view odds_closing_lines / a captured_at <= predicted_at filter pick
the right capture later (D-028 A-8).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from presagio.ingestion.client import RunCounter
from presagio.ingestion.odds_math import PRICE_QUANTUM
from presagio.ingestion.sources.base import Provenance, QualityIssue, SourceFormatError

# Values allowed by the odds_snapshots CHECK constraints.
MARKETS = ("h2h", "spreads", "totals")
SIDES_BY_MARKET: Mapping[str, tuple[str, str]] = {
    "h2h": ("home", "away"),
    "spreads": ("home", "away"),
    "totals": ("over", "under"),
}
CAPTURE_KINDS = ("live", "historical")
# Keys of OddsBatch.skipped.
SKIPPED_STARTED_EVENTS = "started_events"
SKIPPED_STARTED_OUTCOMES = "started_outcomes"
SKIPPED_REJECTED_OUTCOMES = "rejected_outcomes"


@dataclass(frozen=True)
class OddsOutcomeRecord:
    """One bookmaker's decimal price for one side of one market of an event."""

    bookmaker_key: str
    bookmaker_last_update: datetime
    market_key: str
    outcome_name: str
    outcome_side: str
    price_decimal: Decimal
    point: Decimal | None

    def __post_init__(self) -> None:
        """Refuse what the odds_snapshots CHECKs would refuse, before any SQL runs."""
        sides = SIDES_BY_MARKET.get(self.market_key)
        if sides is None:
            raise ValueError(f"unknown market {self.market_key!r}; expected one of {MARKETS}")
        if self.outcome_side not in sides:
            raise ValueError(f"side {self.outcome_side!r} is not valid for {self.market_key}")
        if (self.market_key == "h2h") != (self.point is None):
            raise ValueError(f"{self.market_key}: point must be set exactly when not h2h")
        if self.price_decimal <= 1 or self.price_decimal != self.price_decimal.quantize(
            PRICE_QUANTUM
        ):
            raise ValueError(f"price {self.price_decimal} must be > 1 with at most 4 decimals")
        if self.bookmaker_last_update.tzinfo is None:
            raise ValueError("bookmaker_last_update must be timezone-aware")


@dataclass(frozen=True)
class OddsEventRecord:
    """A game as the provider lists it, with the prices of this capture (may be empty)."""

    source_event_id: str
    home_team_name: str
    away_team_name: str
    commence_time: datetime
    outcomes: tuple[OddsOutcomeRecord, ...] = ()

    def __post_init__(self) -> None:
        """Times must be timezone-aware (schema 1-2)."""
        if self.commence_time.tzinfo is None:
            raise ValueError("commence_time must be timezone-aware")


@dataclass(frozen=True)
class OddsBatch:
    """Everything one capture produced.

    source is the value of odds_events.source / odds_snapshots.source; provenance.source is
    the endpoint. captured_at is the moment these prices describe the market (the receive
    time for live captures). rejected maps events the provider listed but we could not use to
    the reason; skipped counts prices left out on purpose (SKIPPED_* keys); quota keeps the
    provider's usage headers for the run record.
    """

    source: str
    captured_at: datetime
    capture_kind: str
    events: tuple[OddsEventRecord, ...]
    provenance: Provenance
    capture_label: str | None = None
    rejected: Mapping[str, str] = field(default_factory=dict)
    skipped: Mapping[str, int] = field(default_factory=dict)
    issues: tuple[QualityIssue, ...] = ()
    quota: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """captured_at must be aware and capture_kind valid."""
        if self.captured_at.tzinfo is None:
            raise ValueError("captured_at must be timezone-aware")
        if self.capture_kind not in CAPTURE_KINDS:
            raise ValueError(f"capture_kind must be one of {CAPTURE_KINDS}")

    @property
    def outcome_count(self) -> int:
        """Prices in the batch (one odds_snapshots row each)."""
        return sum(len(event.outcomes) for event in self.events)


@runtime_checkable
class OddsSource(Protocol):
    """Gives the current odds (and the event list) of upcoming games for one sport."""

    name: str

    def fetch_odds(
        self, run: RunCounter | None = None, *, capture_label: str | None = None
    ) -> OddsBatch:
        """Capture current prices of every listed event; started events carry no prices."""
        ...

    def fetch_events(self, run: RunCounter | None = None) -> OddsBatch:
        """List upcoming events without prices (for team-name checks and early mapping)."""
        ...


def drop_started_prices(
    events: tuple[OddsEventRecord, ...], captured_at: datetime
) -> tuple[tuple[OddsEventRecord, ...], dict[str, int]]:
    """Strip prices from events that had started by captured_at (schema 7-2: no live odds).

    The events themselves stay so odds_events still learns their latest commence_time.
    Returns the events and {SKIPPED_STARTED_EVENTS: n, SKIPPED_STARTED_OUTCOMES: m}.
    """
    kept: list[OddsEventRecord] = []
    started_events = started_outcomes = 0
    for event in events:
        if event.commence_time <= captured_at and event.outcomes:
            started_events += 1
            started_outcomes += len(event.outcomes)
            event = replace(event, outcomes=())
        kept.append(event)
    return tuple(kept), {
        SKIPPED_STARTED_EVENTS: started_events,
        SKIPPED_STARTED_OUTCOMES: started_outcomes,
    }


def parse_utc(value: Any, where: str) -> datetime:
    """Parse an ISO-8601 time that must carry an offset (e.g. '...Z'), returned in UTC."""
    if not isinstance(value, str):
        raise SourceFormatError(f"{where}: expected an ISO time string, got {value!r}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise SourceFormatError(f"{where}: {value!r} is not an ISO time") from None
    if parsed.tzinfo is None:
        raise SourceFormatError(f"{where}: time {value!r} has no UTC offset")
    return parsed.astimezone(UTC)
