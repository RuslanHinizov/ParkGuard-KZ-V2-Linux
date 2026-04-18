"""
migrations/alembic/env.py

Alembic environment — uses the synchronous driver (psycopg2) via
`DATABASE_URL_SYNC`. Async engine lives in shared.db; this file
reads models from `shared.db.Base.metadata` so autogenerate picks
up every ORM model exported by any service.

To add a new model set, import it into `_import_models()` below.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# --------------------------------------------------------------------------- #
# Make `shared` importable when alembic is run from the repo root or from
# inside the event-api container (where /app/python-services is on PYTHONPATH).
# --------------------------------------------------------------------------- #
_here = Path(__file__).resolve()
_repo_root = _here.parents[2]
_python_services = _repo_root / "python-services"
for p in (str(_python_services), str(_repo_root)):
    if p not in sys.path:
        sys.path.insert(0, p)

from shared.db import Base  # noqa: E402 — path must be set first


# --------------------------------------------------------------------------- #
# Alembic config
# --------------------------------------------------------------------------- #
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Resolve DB URL from env (set by docker-compose / .env).
db_url = os.getenv("DATABASE_URL_SYNC")
if not db_url:
    raise RuntimeError(
        "DATABASE_URL_SYNC environment variable is required for Alembic. "
        "See .env.example."
    )
config.set_main_option("sqlalchemy.url", db_url)


def _import_models() -> None:
    """
    Import all ORM model modules so Base.metadata is populated.

    The event-api folder ``event-api`` contains a ``src/models`` package that
    is added to ``sys.path`` via the Dockerfile PYTHONPATH. In the repo layout
    the import path becomes ``src.models.<module>``.

    Fails silently during early scaffolding (pre Adım 2): the initial
    PostGIS-extension migration does not need any ORM table metadata.
    """
    # event-api itself is on PYTHONPATH inside the container; outside the
    # container (host-side autogenerate) we prepend it here for reliability.
    event_api_root = _repo_root / "python-services" / "event-api"
    if event_api_root.is_dir() and str(event_api_root) not in sys.path:
        sys.path.insert(0, str(event_api_root))

    try:
        from src.models import (  # noqa: F401
            audit_log,
            camera,
            cvi,
            penalty_card,
            plate_history,
            violation,
            zone,
        )
    except ImportError:
        # Models are introduced incrementally (step 2+). Safe to skip.
        pass


_import_models()
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emits SQL to stdout)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection."""
    section = config.get_section(config.config_ini_section) or {}
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_object=_include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


def _include_object(obj, name, type_, reflected, compare_to):  # noqa: ANN001
    """Skip PostGIS-owned system tables during autogenerate."""
    if type_ == "table" and name in {
        "spatial_ref_sys",
        "geography_columns",
        "geometry_columns",
        "raster_columns",
        "raster_overviews",
    }:
        return False
    return True


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
