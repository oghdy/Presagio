"""Alembic migrations: up/down/up on an empty database, model parity, and the design DDL."""

import os
import re
import subprocess
import sys
import warnings
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import URL, Connection, text

from presagio.db.models import VIEW_NAMES, Base
from presagio.db.session import make_engine

REPO_ROOT = Path(__file__).resolve().parents[2]
DESIGN_DOC = REPO_ROOT / "docs" / "design" / "schema-v1.md"
TABLE_NAMES = frozenset(Base.metadata.tables)
# Known, harmless Alembic warning: PostgreSQL stores the generated column expression with
# explicit ::text casts, so Alembic's crude text comparison never matches the model's text.
COMPUTED_WARNING = "Computed default on games.game_type cannot be modified"
HEAD = "0002"
# CHECK constraints in appendix A (revision 0001) and in chapter 9-7 (revision 0002).
V1_CHECKS = 60
MODELING_CHECKS = 48
MODELING_TABLES = frozenset({"features", "model_versions", "predictions", "evaluations"})
MODELING_VIEWS = frozenset({"predictions_recommendation_basis"})
# The one partial unique index of the modeling layer (schema v1 9-4, D-033).
PARTIAL_UNIQUE_INDEX = "uq_predictions_one_recommendation_basis"
# CREATE UNIQUE INDEX ... ON [public.]<table> [USING btree] (<columns>) WHERE <predicate>, as
# the design DDL writes it and as pg_indexes.indexdef prints it.
PARTIAL_UNIQUE_INDEX_RE = re.compile(
    r"CREATE UNIQUE INDEX (?P<name>\w+) ON (?:public\.)?(?P<table>\w+)(?: USING btree)? "
    r"\((?P<columns>[^)]*)\) WHERE (?P<where>.+?);?$"
)


def _public_relations(conn: Connection) -> dict[str, str]:
    """Return {name: relkind} for every relation in the public schema."""
    rows = conn.execute(
        text(
            "SELECT relname, relkind FROM pg_class "
            "WHERE relnamespace = 'public'::regnamespace ORDER BY relname"
        )
    )
    return {name: kind for name, kind in rows}


AlembicConfig = Callable[[Connection], Config]


def _upgrade(alembic_config: AlembicConfig, url: URL, revision: str = "head") -> None:
    """Run `alembic upgrade <revision>` on the database at url."""
    engine = make_engine(url)
    with engine.begin() as conn:
        command.upgrade(alembic_config(conn), revision)
    engine.dispose()


def test_upgrade_downgrade_upgrade(
    make_test_database: Callable[[], URL], alembic_config: AlembicConfig
) -> None:
    """An empty database goes to head, back to base with nothing left, and to head again."""
    url = make_test_database()
    engine = make_engine(url)
    try:
        for _ in range(2):
            with engine.begin() as conn:
                command.upgrade(alembic_config(conn), "head")
            with engine.connect() as conn:
                relations = _public_relations(conn)
                version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            tables = {name for name, kind in relations.items() if kind == "r"}
            views = {name for name, kind in relations.items() if kind == "v"}
            assert tables == TABLE_NAMES | {"alembic_version"}
            assert views == VIEW_NAMES
            assert version == HEAD

            with engine.begin() as conn:
                command.downgrade(alembic_config(conn), "base")
            with engine.connect() as conn:
                # Only Alembic's own (now empty) version table may remain: no tables, views,
                # sequences or indexes left behind.
                assert set(_public_relations(conn)) == {"alembic_version", "alembic_version_pkc"}
                assert conn.execute(text("SELECT count(*) FROM alembic_version")).scalar() == 0
    finally:
        engine.dispose()


def test_downgrade_one_step_removes_only_the_modeling_layer(
    make_test_database: Callable[[], URL], alembic_config: AlembicConfig
) -> None:
    """head -> downgrade -1 -> head: 0002 drops exactly its four tables and view, then returns."""
    url = make_test_database()
    engine = make_engine(url)
    try:
        with engine.begin() as conn:
            command.upgrade(alembic_config(conn), "head")
        with engine.connect() as conn:
            at_head = _public_relations(conn)
        with engine.begin() as conn:
            command.downgrade(alembic_config(conn), "-1")
        with engine.connect() as conn:
            at_0001 = _public_relations(conn)
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0001"
        dropped = set(at_head) - set(at_0001)
        modeling = MODELING_TABLES | MODELING_VIEWS
        assert modeling <= dropped
        # Everything else 0002 dropped belongs to those tables (their indexes and sequences).
        assert not set(at_0001) - set(at_head)
        assert {at_head[name] for name in dropped - modeling} <= {"i", "S"}
        with engine.begin() as conn:
            command.upgrade(alembic_config(conn), "head")
        with engine.connect() as conn:
            assert _public_relations(conn) == at_head
    finally:
        engine.dispose()


def test_models_match_migrations(
    make_test_database: Callable[[], URL], alembic_config: AlembicConfig
) -> None:
    """`alembic check` finds no difference between the models and the migrated database."""
    url = make_test_database()
    _upgrade(alembic_config, url)
    engine = make_engine(url)
    try:
        with engine.connect() as conn, warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=COMPUTED_WARNING)
            command.check(alembic_config(conn))  # raises AutogenerateDiffsDetected on a diff
    finally:
        engine.dispose()


def test_alembic_cli_reads_database_url(make_test_database: Callable[[], URL]) -> None:
    """`alembic upgrade head` / `downgrade base` from the shell use DATABASE_URL via settings."""
    url = make_test_database()
    env = {**os.environ, "DATABASE_URL": url.render_as_string(hide_password=False)}
    for args in (["upgrade", "head"], ["downgrade", "base"], ["upgrade", "head"]):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    engine = make_engine(url)
    with engine.connect() as conn:
        assert set(_public_relations(conn)) >= TABLE_NAMES | VIEW_NAMES
    engine.dispose()


def _sql_block(section: str) -> str:
    """Return the only SQL block in one section of docs/design/schema-v1.md."""
    blocks = re.findall(r"```sql\n(.*?)```", section, re.DOTALL)
    assert len(blocks) == 1, f"expected one SQL block, found {len(blocks)}"
    return blocks[0]


def _design_ddl() -> str:
    """Return the SQL block of appendix A (the approved schema v1 DDL, revision 0001)."""
    return _sql_block(DESIGN_DOC.read_text(encoding="utf-8").split("## 부록 A", 1)[1])


def _modeling_ddl() -> str:
    """Return the SQL block of chapter 9 (the modeling layer DDL, revision 0002)."""
    chapter = DESIGN_DOC.read_text(encoding="utf-8").split("\n## 9. ", 1)[1]
    return _sql_block(chapter.split("\n## 10. ", 1)[0])


def _catalog(conn: Connection) -> dict[str, Any]:
    """Describe the public schema by definition, ignoring generated constraint names."""
    columns = conn.execute(
        text(
            """
            SELECT c.relname, a.attnum, a.attname, format_type(a.atttypid, a.atttypmod),
                   a.attnotnull, a.attidentity, a.attgenerated,
                   pg_get_expr(d.adbin, d.adrelid)
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
            WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'v')
              AND c.relname <> 'alembic_version' AND a.attnum > 0 AND NOT a.attisdropped
            """
        )
    ).all()
    constraints = conn.execute(
        text(
            """
            SELECT conrelid::regclass::text, contype, pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE connamespace = 'public'::regnamespace
              AND conrelid::regclass::text <> 'alembic_version'
            """
        )
    ).all()
    indexes = conn.execute(
        text(
            """
            SELECT tablename, regexp_replace(indexdef, 'INDEX \\S+ ON', 'INDEX ON')
            FROM pg_indexes WHERE schemaname = 'public' AND tablename <> 'alembic_version'
            """
        )
    ).all()
    # Indexes that are not backing a constraint carry the names the design gave them.
    named_indexes = conn.execute(
        text(
            """
            SELECT i.indexrelid::regclass::text FROM pg_index i
            JOIN pg_class c ON c.oid = i.indrelid
            WHERE c.relnamespace = 'public'::regnamespace AND c.relname <> 'alembic_version'
              AND NOT EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conindid = i.indexrelid)
            """
        )
    ).scalars()
    views = conn.execute(
        text(
            "SELECT relname, pg_get_viewdef(oid, true) FROM pg_class "
            "WHERE relnamespace = 'public'::regnamespace AND relkind = 'v'"
        )
    ).all()
    return {
        "columns": sorted(tuple(r) for r in columns),
        "constraints": Counter(tuple(r) for r in constraints),
        "indexes": Counter(tuple(r) for r in indexes),
        "named_indexes": sorted(named_indexes),
        "views": dict(views),
    }


def _difference(key: str, got: Any, want: Any) -> str:
    """Describe what the migrated database has that the other one lacks, and the reverse."""
    if isinstance(want, Counter):
        extra, missing = sorted((got - want).elements()), sorted((want - got).elements())
    elif isinstance(want, dict):
        extra = sorted((k, v) for k, v in got.items() if want.get(k) != v)
        missing = sorted((k, v) for k, v in want.items() if got.get(k) != v)
    else:
        extra = sorted(set(got) - set(want))
        missing = sorted(set(want) - set(got))
    return f"{key} differ\n  only in migration: {extra}\n  only in the other: {missing}"


def _assert_migration_matches(
    make_test_database: Callable[[], URL],
    alembic_config: AlembicConfig,
    revision: str,
    ddl: list[str],
    checks: int,
) -> None:
    """Migrate one temp DB to revision, run the design DDL blocks on another, compare catalogs."""
    migrated_url, design_url = make_test_database(), make_test_database()
    _upgrade(alembic_config, migrated_url, revision)
    design_engine = make_engine(design_url)
    with design_engine.begin() as conn:
        for block in ddl:
            conn.exec_driver_sql(block)
    migrated_engine = make_engine(migrated_url)
    try:
        with migrated_engine.connect() as migrated, design_engine.connect() as design:
            got, want = _catalog(migrated), _catalog(design)
        for key in want:
            assert got[key] == want[key], _difference(key, got[key], want[key])
        assert sum(1 for r in want["constraints"].elements() if r[1] == "c") == checks
    finally:
        migrated_engine.dispose()
        design_engine.dispose()


def test_migration_matches_design_ddl(
    make_test_database: Callable[[], URL], alembic_config: AlembicConfig
) -> None:
    """Revision 0001 builds the same columns, constraints, indexes and views as appendix A."""
    _assert_migration_matches(
        make_test_database, alembic_config, "0001", [_design_ddl()], V1_CHECKS
    )


def test_head_matches_design_ddl(
    make_test_database: Callable[[], URL], alembic_config: AlembicConfig
) -> None:
    """Head (0002) is appendix A plus the chapter 9-7 modeling DDL, catalog for catalog."""
    _assert_migration_matches(
        make_test_database,
        alembic_config,
        HEAD,
        [_design_ddl(), _modeling_ddl()],
        V1_CHECKS + MODELING_CHECKS,
    )


def _design_index_statement(name: str) -> str:
    """Return the CREATE INDEX statement for one index in the chapter 9-7 DDL, one line."""
    match = re.search(rf"CREATE UNIQUE INDEX {name}\s.*?;", _modeling_ddl(), re.DOTALL)
    assert match, f"{name} is not in the chapter 9 DDL"
    return " ".join(match.group(0).split())


def _parse_partial_unique_index(statement: str) -> tuple[str, str, tuple[str, ...], str]:
    """Return (name, table, columns, WHERE predicate) of a CREATE UNIQUE INDEX ... WHERE."""
    match = PARTIAL_UNIQUE_INDEX_RE.fullmatch(" ".join(statement.split()))
    assert match, f"not a partial unique index statement: {statement}"
    columns = tuple(c.strip() for c in match["columns"].split(","))
    return match["name"], match["table"], columns, match["where"].strip()


def _indexdef(conn: Connection, name: str) -> str:
    """Return pg_indexes.indexdef of the named index in the public schema."""
    return conn.execute(
        text("SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' AND indexname = :n"),
        {"n": name},
    ).scalar_one()


def test_partial_unique_index_matches_design(
    make_test_database: Callable[[], URL], alembic_config: AlembicConfig
) -> None:
    """M5: the recommendation-basis index has the design's name, columns and WHERE clause, in
    the migrated database and in tables built straight from the models alike."""
    # The catalog comparison strips index names and `alembic check` does not compare index
    # predicates, so a WHERE clause edited in the model alone would otherwise go unnoticed.
    design = _parse_partial_unique_index(_design_index_statement(PARTIAL_UNIQUE_INDEX))
    assert design[:2] == (PARTIAL_UNIQUE_INDEX, "predictions")
    migrated_url, models_url = make_test_database(), make_test_database()
    _upgrade(alembic_config, migrated_url)
    models_engine = make_engine(models_url)
    Base.metadata.create_all(models_engine)
    migrated_engine = make_engine(migrated_url)
    try:
        for label, engine in (("migration", migrated_engine), ("models", models_engine)):
            with engine.connect() as conn:
                indexdef = _indexdef(conn, PARTIAL_UNIQUE_INDEX)
            assert _parse_partial_unique_index(indexdef) == design, f"{label}: {indexdef}"
    finally:
        migrated_engine.dispose()
        models_engine.dispose()


def _named_constraints(conn: Connection) -> set[tuple[str, str, str, str]]:
    """Return (table, name, type, definition) for every constraint on the schema v1 tables."""
    rows = conn.execute(
        text(
            "SELECT conrelid::regclass::text, conname, contype, pg_get_constraintdef(oid) "
            "FROM pg_constraint WHERE connamespace = 'public'::regnamespace "
            "AND conrelid::regclass::text <> 'alembic_version'"
        )
    )
    return {tuple(row) for row in rows}


def test_model_constraints_match_migration(
    make_test_database: Callable[[], URL], alembic_config: AlembicConfig
) -> None:
    """Tables built straight from the models carry the same named constraints as the migration."""
    # `alembic check` ignores CHECK expressions (even with checkconstraint_byname, which only
    # matches names), so a CHECK edited in a model alone would slip into the next autogenerate.
    migrated_url, models_url = make_test_database(), make_test_database()
    _upgrade(alembic_config, migrated_url)
    models_engine = make_engine(models_url)
    Base.metadata.create_all(models_engine)
    migrated_engine = make_engine(migrated_url)
    try:
        with migrated_engine.connect() as migrated, models_engine.connect() as models:
            got, want = _named_constraints(migrated), _named_constraints(models)
        assert got == want, _difference("constraints", got, want)
        assert sum(1 for row in want if row[2] == "c") == V1_CHECKS + MODELING_CHECKS
    finally:
        migrated_engine.dispose()
        models_engine.dispose()
