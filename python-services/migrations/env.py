"""
python-services/migrations/env.py

Alembic environment — runs for both online (live DB) and offline (SQL
script generation) modes.

Model discovery
---------------
All ORM models that contribute to the public schema are imported here
so that Base.metadata reflects the complete table set.  Alembic
autogenerate then diffs metadata against the actual DB.

Because the two service directories use hyphens in their names
(event-api, violation-service), which are not valid Python identifiers,
we load their `src/models/__init__.py` files via importlib under
globally-unique module names so they register with the shared Base.

DATABASE_URL_SYNC
-----------------
The sync URL (psycopg2) is read from the DATABASE_URL_SYNC environment
variable; it falls back to the value in alembic.ini.  Never hard-code
passwords here — use the .env or docker-compose environment.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# ---------------------------------------------------------------------------
# Make python-services/ importable (so `from shared.db import Base` works).
# ---------------------------------------------------------------------------
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here)   # python-services/
if _root not in sys.path:
    sys.path.insert(0, _root)

# ---------------------------------------------------------------------------
# Bootstrap env vars so pydantic Settings validates without a full .env file.
# Alembic only uses DATABASE_URL_SYNC; the rest are dummies.
# ---------------------------------------------------------------------------
os.environ.setdefault("POSTGRES_PASSWORD",   "alembic-dummy")
os.environ.setdefault("REDIS_PASSWORD",      "alembic-dummy")
os.environ.setdefault("REDIS_URL",           "redis://:alembic-dummy@localhost:6379/0")
os.environ.setdefault("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
os.environ.setdefault("MINIO_ENDPOINT",      "localhost:9000")
os.environ.setdefault("MINIO_ROOT_USER",     "minioadmin")
os.environ.setdefault("MINIO_ROOT_PASSWORD", "alembic-dummy")


def _load_model_file(service_dir: str, *rel_parts: str) -> None:
    """
    Load a single model file by its path relative to <service_dir>/src/models/
    as a globally-unique module name, so it self-registers with Base.metadata.

    Loading individual files (not __init__.py) avoids the `src` package-name
    collision between event-api and violation-service — each model file only
    imports from `shared.*` and `sqlalchemy.*`.
    """
    abs_path = os.path.join(_root, service_dir, "src", "models", *rel_parts)
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"model file not found: {abs_path}")
    safe_name = (
        "parkguard_models."
        + service_dir.replace("-", "_")
        + "."
        + ".".join(p.replace(".py", "") for p in rel_parts)
    )
    spec = importlib.util.spec_from_file_location(safe_name, abs_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[safe_name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]


# Import shared Base first (establishes the metadata registry).
from shared.db import Base  # noqa: E402

# event-api models: Camera → Zone (FK cameras) → AuditLog
# NOTE: event-api/src/models/violation.py is a read-only stub without FK
# constraints and deliberately skipped here — the canonical `violations`
# table schema comes from violation-service below.
_load_model_file("event-api", "camera.py")
_load_model_file("event-api", "audit_log.py")
_load_model_file("event-api", "zone.py")

# violation-service models: CVIRecord (FK cameras) → Violation (FK cameras+zones+cvi_records)
_load_model_file("violation-service", "cvi.py")
_load_model_file("violation-service", "violation.py")

target_metadata = Base.metadata

# ---------------------------------------------------------------------------
# Alembic config object
# ---------------------------------------------------------------------------
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override URL from environment if provided.
_db_url = os.environ.get("DATABASE_URL_SYNC")
if _db_url:
    config.set_main_option("sqlalchemy.url", _db_url)


# ---------------------------------------------------------------------------
# Offline mode — emit SQL to stdout / file.
# ---------------------------------------------------------------------------
def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_schemas=False,
    )
    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online mode — connect and apply.
# ---------------------------------------------------------------------------
def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_schemas=False,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
