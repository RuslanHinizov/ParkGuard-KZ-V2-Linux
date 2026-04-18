"""postgis extension

Revision ID: 0001_postgis
Revises:
Create Date: 2026-04-18 00:00:00.000000

Enables the PostGIS extension so later migrations can declare
GEOMETRY columns (zones.polygon). Tables are added in step 2.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001_postgis"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")


def downgrade() -> None:
    # Extension drops are destructive for any GEOMETRY columns — require
    # explicit operator action.
    op.execute("DROP EXTENSION IF EXISTS postgis")
