"""Alembic environment: migrate the database named by DATABASE_URL (presagio.config)."""

from logging.config import fileConfig
from typing import Any, Literal

from alembic import context
from sqlalchemy import Connection

from presagio.config import get_settings
from presagio.db.models import VIEW_NAMES, Base
from presagio.db.session import make_engine

config = context.config

# Tests drive Alembic programmatically and keep their own logging setup.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def include_object(
    obj: Any,
    name: str | None,
    type_: Literal[
        "schema", "table", "column", "index", "unique_constraint", "foreign_key_constraint"
    ],
    reflected: bool,
    compare_to: Any,
) -> bool:
    """Keep the raw-SQL views out of autogenerate, which would otherwise see no model for them."""
    return not (type_ == "table" and name in VIEW_NAMES)


def _run(connection: Connection) -> None:
    """Configure the migration context on a live connection and run the migrations."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    """Emit SQL to stdout (`alembic upgrade head --sql`) without connecting."""
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run against a connection passed in by a caller (tests) or one built from settings."""
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    engine = make_engine()
    try:
        with engine.connect() as connection:
            _run(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
