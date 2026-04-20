"""Initial schema — cameras, zones, cvi_records, violations, audit_log.

Revision ID: 0001
Revises:
Create Date: 2026-04-20

Table creation order respects foreign-key dependencies:
  cameras → cvi_records (FK cameras)
  cameras → zones      (FK cameras)
  cameras + zones + cvi_records → violations
  audit_log (no FK — standalone)

The `zones.polygon_wkt` column uses PostGIS GEOMETRY(POLYGON,0).
`CREATE EXTENSION IF NOT EXISTS postgis` is emitted first so the type
is always available before table DDL.

Downgrade reverses creation order so FK constraints are satisfied.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # PostGIS extension — must exist before the zones table DDL.
    # The IF NOT EXISTS guard makes this re-runnable (e.g. after a failed
    # first migration attempt).
    # ------------------------------------------------------------------
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    # ------------------------------------------------------------------
    # cameras
    # ------------------------------------------------------------------
    op.create_table(
        "cameras",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("rtsp_substream_url", sa.Text(), nullable=False),
        sa.Column("rtsp_mainstream_url", sa.Text(), nullable=True),
        sa.Column("location_description", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("config", postgresql.JSONB().with_variant(sa.JSON(), "sqlite"),
                  nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
    )

    # ------------------------------------------------------------------
    # cvi_records
    # ------------------------------------------------------------------
    op.create_table(
        "cvi_records",
        sa.Column("cvi_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("camera_id", sa.String(32), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("plate_text", sa.String(16), nullable=True),
        sa.Column("plate_confidence", sa.Float(), nullable=True),
        sa.Column("dominant_class", sa.String(20), nullable=True),
        sa.Column("observations_count", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["camera_id"], ["cameras.id"],
            name="fk_cvi_records_camera_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_cvi_records_camera_id",   "cvi_records", ["camera_id"])
    op.create_index("ix_cvi_records_last_seen",    "cvi_records", ["last_seen"])
    op.create_index("ix_cvi_records_plate_text",   "cvi_records", ["plate_text"],
                    postgresql_where=sa.text("plate_text IS NOT NULL"))

    # ------------------------------------------------------------------
    # zones  (PostGIS GEOMETRY column)
    # ------------------------------------------------------------------
    op.create_table(
        "zones",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("camera_id", sa.String(32), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        # On Postgres: GEOMETRY(POLYGON,0) via geoalchemy2.
        # On SQLite test builds: TEXT (WKT stored as plain string).
        sa.Column("polygon_wkt",
                  sa.Text().with_variant(
                      # Use raw SA text type for the column; geoalchemy2's
                      # DDL listener handles the actual Postgres type.
                      sa.Text(),
                      "postgresql",
                  ),
                  nullable=False),
        sa.Column("zone_type", sa.String(32), nullable=False,
                  server_default=sa.text("'no_parking'")),
        sa.Column("threshold_seconds", sa.Integer(), nullable=False,
                  server_default=sa.text("20")),
        sa.Column("exit_confirm_seconds", sa.Integer(), nullable=False,
                  server_default=sa.text("10")),
        sa.Column("cooldown_seconds", sa.Integer(), nullable=False,
                  server_default=sa.text("10")),
        sa.Column("color_hex", sa.String(7), nullable=False,
                  server_default=sa.text("'#FF0000'")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["camera_id"], ["cameras.id"],
            name="fk_zones_camera_id",
            ondelete="CASCADE",
        ),
    )
    # Replace the TEXT column with a proper GEOMETRY column on Postgres.
    # geoalchemy2 doesn't hook autogenerate cleanly for new tables, so we
    # use raw DDL.
    op.execute(
        "ALTER TABLE zones "
        "ALTER COLUMN polygon_wkt TYPE geometry(POLYGON,0) "
        "USING polygon_wkt::geometry"
    )
    op.create_index("ix_zones_camera_id", "zones", ["camera_id"])
    op.create_index("ix_zones_enabled",   "zones", ["enabled"])

    # ------------------------------------------------------------------
    # violations
    # ------------------------------------------------------------------
    op.create_table(
        "violations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("camera_id", sa.String(32), nullable=False),
        sa.Column("zone_id", sa.Integer(), nullable=False),
        sa.Column("identity_key", sa.String(128), nullable=False),
        sa.Column("first_seen_in_zone", sa.DateTime(timezone=True), nullable=False),
        sa.Column("violation_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cvi_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("cycle_id", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("plate_text", sa.String(16), nullable=True),
        sa.Column("plate_confidence", sa.Float(), nullable=True),
        sa.Column("plate_format", sa.String(16), nullable=True),
        sa.Column("plate_region_code", sa.String(4), nullable=True),
        sa.Column("plate_valid_format", sa.Boolean(), nullable=False,
                  server_default=sa.text("FALSE")),
        sa.Column("is_diplomatic", sa.Boolean(), nullable=False,
                  server_default=sa.text("FALSE")),
        sa.Column("vehicle_class", sa.String(20), nullable=True),
        sa.Column("exit_confirmed_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("snapshot_vehicle_url", sa.Text(), nullable=True),
        sa.Column("snapshot_plate_url", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default=sa.text("'pending'")),
        sa.Column("reviewed_by", sa.String(64), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("penalty_card_url", sa.Text(), nullable=True),
        sa.Column("bbox",
                  postgresql.JSONB().with_variant(sa.JSON(), "sqlite"),
                  nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["camera_id"], ["cameras.id"],
            name="fk_violations_camera_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["zone_id"], ["zones.id"],
            name="fk_violations_zone_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["cvi_id"], ["cvi_records.cvi_id"],
            name="fk_violations_cvi_id",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "camera_id", "zone_id", "identity_key", "cycle_id",
            name="uniq_violation_identity_cycle",
        ),
    )
    op.create_index("ix_violations_camera_id",      "violations", ["camera_id"])
    op.create_index("ix_violations_zone_id",         "violations", ["zone_id"])
    op.create_index("ix_violations_status",          "violations", ["status"])
    op.create_index("ix_violations_violation_time",  "violations", ["violation_time"])
    op.create_index("ix_violations_plate_text",      "violations", ["plate_text"],
                    postgresql_where=sa.text("plate_text IS NOT NULL"))

    # ------------------------------------------------------------------
    # audit_log
    # ------------------------------------------------------------------
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("operator_name", sa.String(64), nullable=True),
        sa.Column("target_type", sa.String(32), nullable=True),
        sa.Column("target_id", sa.String(64), nullable=True),
        sa.Column("payload",
                  postgresql.JSONB().with_variant(sa.JSON(), "sqlite"),
                  nullable=True),
        sa.Column("ip_address",
                  postgresql.INET().with_variant(sa.String(45), "sqlite"),
                  nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("ix_audit_log_action",      "audit_log", ["action"])
    op.create_index("ix_audit_log_target",      "audit_log", ["target_type", "target_id"])
    op.create_index("ix_audit_log_created_at",  "audit_log", ["created_at"])


def downgrade() -> None:
    # Drop in reverse dependency order.
    op.drop_table("audit_log")
    op.drop_table("violations")
    op.drop_table("zones")
    op.drop_table("cvi_records")
    op.drop_table("cameras")
    # Leave postgis extension — it may be used by other schemas.
