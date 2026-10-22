"""Declarative base and metadata naming convention shared by every table model."""

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, MetaData, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase

# Deterministic constraint names, so migrations and `alembic check` agree on every name.
# CHECK constraints must be given a short name; it becomes ck_<table>_<name>. Declare them in
# __table_args__, not on a column: Alembic autogenerate silently drops column-level CHECKs.
NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}


class Base(DeclarativeBase):
    """Base class for the schema v1 tables (docs/design/schema-v1.md)."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    # Schema rule 1-1: text instead of varchar, and every timestamp is timestamptz.
    type_annotation_map = {
        str: Text(),
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB(),
    }


def nonnegative(column: str) -> CheckConstraint:
    """Return the table-level CHECK `column >= 0`, named ck_<table>_<column>_nonnegative."""
    return CheckConstraint(f"{column} >= 0", name=f"{column}_nonnegative")
