"""Alembic environment, wired for GeoAlchemy2.

GeoAlchemy2 footguns this file defends against:

1. ``target_metadata`` is ``veritas_eudr.db.Base.metadata`` and we import the ORM
   module (``veritas_eudr.db``) so every table -- including ``plots`` with its
   ``geometry(Geometry,4326)`` column -- is registered on that metadata before
   autogenerate runs.

2. Both ``include_object`` and ``render_item`` from
   ``geoalchemy2.alembic_helpers`` are passed into ``context.configure``:
     - ``include_object`` skips PostGIS-internal tables (spatial_ref_sys,
       geometry_columns, ...) so autogenerate never tries to drop them.
     - ``render_item`` emits ``from geoalchemy2 import Geometry`` and the correct
       ``Geometry(...)`` repr for spatial columns in generated scripts.

3. ``process_revision_directives`` is the ``geoalchemy2.alembic_helpers.writer``
   Rewriter, which rewrites the auto-generated table/column/index ops into the
   geospatial variants so the auto-created spatial index is NOT spuriously
   emitted as a separate (and then dropped) index.

The DB URL is read from ``veritas_eudr.config.get_settings().database_url``
(env-overridable via ``VERITAS_DATABASE_URL`` / ``DATABASE_URL``), never a
hardcoded literal. A ``sqlalchemy.url`` set explicitly on the Config (e.g. by a
test harness) takes precedence.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from geoalchemy2 import alembic_helpers
from sqlalchemy import engine_from_config, pool

# Importing the ORM module registers every table on Base.metadata. Do NOT remove.
from veritas_eudr import db as _db  # noqa: F401  (import for side effect)
from veritas_eudr.config import get_settings

# Alembic Config object, providing access to values within the .ini file.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The metadata autogenerate compares against -- the single source of truth.
target_metadata = _db.Base.metadata


def _resolve_url() -> str:
    """Resolve the DB URL: explicit Config override > env > settings default."""
    explicit = config.get_main_option("sqlalchemy.url")
    if explicit:
        return explicit
    env_url = os.environ.get("VERITAS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if env_url:
        return env_url
    return get_settings().database_url


# Shared keyword arguments for both offline and online configuration. The
# GeoAlchemy2 hooks must be present in BOTH paths.
_GEOALCHEMY2_HOOKS = {
    "include_object": alembic_helpers.include_object,
    "render_item": alembic_helpers.render_item,
    "process_revision_directives": alembic_helpers.writer,
}


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL, no DBAPI connection)."""
    url = _resolve_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        **_GEOALCHEMY2_HOOKS,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against a live connection."""
    # Build the engine from the alembic config section, but force in the resolved
    # URL so we never depend on a literal in alembic.ini.
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _resolve_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            **_GEOALCHEMY2_HOOKS,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
