"""Team dictionary tables: teams and team_aliases (schema v1 ch. 5)."""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column

from presagio.db.base import Base


class Team(Base):
    """An NBA franchise keyed by its nba_api team ID."""

    __tablename__ = "teams"

    team_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    tricode: Mapped[str] = mapped_column(unique=True)
    city: Mapped[str]
    nickname: Mapped[str]
    full_name: Mapped[str]
    source: Mapped[str]
    fetched_at: Mapped[datetime]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        CheckConstraint("team_id > 0", name="team_id_positive"),
        CheckConstraint("tricode ~ '^[A-Z]{3}$'", name="tricode_format"),
    )


class TeamAlias(Base):
    """A team name as an external source spells it, mapped to our team ID."""

    __tablename__ = "team_aliases"

    source: Mapped[str] = mapped_column(primary_key=True)
    alias: Mapped[str] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.team_id"))
    fetched_at: Mapped[datetime]
    note: Mapped[str | None]

    __table_args__ = (Index("ix_team_aliases_team", "team_id"),)
