"""Betting odds tables: odds_events and odds_snapshots (schema v1 ch. 7)."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    Numeric,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from presagio.db.base import Base


class OddsEvent(Base):
    """A game as the odds provider lists it, optionally matched to one of our games."""

    __tablename__ = "odds_events"

    odds_event_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    source: Mapped[str]
    source_event_id: Mapped[str]
    home_team_name: Mapped[str]
    away_team_name: Mapped[str]
    commence_time: Mapped[datetime]
    game_id: Mapped[str | None] = mapped_column(ForeignKey("games.game_id"))
    match_method: Mapped[str | None]
    matched_at: Mapped[datetime | None]
    fetched_at: Mapped[datetime]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    raw_response_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("raw_api_responses.raw_response_id")
    )

    __table_args__ = (
        CheckConstraint("source IN ('the_odds_api')", name="source_valid"),
        UniqueConstraint("source", "source_event_id", name="uq_odds_events_source_id"),
        CheckConstraint("(game_id IS NULL) = (matched_at IS NULL)", name="matched_iff_game"),
        Index("ix_odds_events_game", "game_id"),
        # Events still waiting to be matched to a game (query Q8).
        Index(
            "ix_odds_events_unmatched", "commence_time", postgresql_where=text("game_id IS NULL")
        ),
    )


class OddsSnapshot(Base):
    """One bookmaker price for one outcome at one capture time; rows are never updated."""

    __tablename__ = "odds_snapshots"

    odds_snapshot_id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    odds_event_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("odds_events.odds_event_id"))
    source: Mapped[str]
    captured_at: Mapped[datetime]
    capture_kind: Mapped[str]
    capture_label: Mapped[str | None]
    commence_time: Mapped[datetime]
    bookmaker_key: Mapped[str]
    bookmaker_last_update: Mapped[datetime]
    market_key: Mapped[str]
    outcome_name: Mapped[str]
    outcome_side: Mapped[str]
    price_decimal: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    point: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    fetched_at: Mapped[datetime]
    raw_response_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("raw_api_responses.raw_response_id")
    )

    __table_args__ = (
        CheckConstraint("source IN ('the_odds_api')", name="source_valid"),
        CheckConstraint("capture_kind IN ('live', 'historical')", name="capture_kind_valid"),
        CheckConstraint("market_key IN ('h2h', 'spreads', 'totals')", name="market_key_valid"),
        CheckConstraint(
            "outcome_side IN ('home', 'away', 'over', 'under')", name="outcome_side_valid"
        ),
        CheckConstraint("price_decimal > 1", name="price_decimal_above_one"),
        UniqueConstraint(
            "odds_event_id",
            "market_key",
            "bookmaker_key",
            "outcome_side",
            "captured_at",
            name="uq_odds_snapshot",
        ),
        # Prices captured after tip-off are leakage; the database refuses them (schema v1 7-2).
        CheckConstraint("captured_at < commence_time", name="captured_before_commence"),
        CheckConstraint(
            "(market_key = 'totals') = (outcome_side IN ('over', 'under'))",
            name="totals_use_over_under",
        ),
        CheckConstraint("(market_key = 'h2h') = (point IS NULL)", name="point_iff_not_h2h"),
        CheckConstraint("captured_at <= fetched_at", name="captured_before_fetched"),
        Index("ix_odds_snapshots_captured", "captured_at"),
        Index("ix_odds_snapshots_raw", "raw_response_id"),
    )
