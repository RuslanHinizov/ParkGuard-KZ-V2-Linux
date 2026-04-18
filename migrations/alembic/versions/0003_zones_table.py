"""zones table with PostGIS polygon

Revision ID: 0003_zones
Revises: 0002_cameras_audit
Create Date: 2026-04-18

Spec §10.5 — zones reference cameras and carry a PostGIS POLYGON that
violation-service reads through GeoAlchemy2 (WKT at the Python layer,
GEOMETRY on disk). `idx_zones_camera` is a partial index on enabled=TRUE
so the zone-lookup fast path (violation-service cache warmup) hits only
live zones.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry

revision: str = "0003_zones"
down_revision: str | None = "0002_cameras_audit"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "zones",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "camera_id",
            sa.String(length=32),
            sa.ForeignKey("cameras.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(
            "zone_type",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'no_parking'"),
        ),
        sa.Column(
            "polygon_wkt",
            Geometry(geometry_type="POLYGON", srid=0, spatial_index=False),
            nullable=False,
        ),
        sa.Column(
            "threshold_seconds",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("20"),
        ),
        sa.Column(
            "exit_confirm_seconds",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("10"),
        ),
        sa.Column(
            "cooldown_seconds",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("10"),
        ),
        sa.Column(
            "color_hex",
            sa.String(length=7),
            nullable=False,
            server_default=sa.text("'#FF0000'"),
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("TRUE"),
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "idx_zones_camera",
        "zones",
        ["camera_id"],
        postgresql_where=sa.text("enabled = TRUE"),
    )


def downgrade() -> None:
    op.drop_index("idx_zones_camera", table_name="zones")
    op.drop_table("zones")
