"""cvi_records + violations tables

Revision ID: 0004_cvi_violations
Revises: 0003_zones
Create Date: 2026-04-18

Spec §10.5. `violations` carries the unique `(camera_id, zone_id,
identity_key, cycle_id)` index which is the last-line duplicate-
prevention guard. Partial indexes filter out NULL plate/region rows so
the plate-text index stays small for the mixed-OCR reality (spec §16.2
expects ~40% unreadable plates in night operation).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0004_cvi_violations"
down_revision: str | None = "0003_zones"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------ cvi_records
    op.create_table(
        "cvi_records",
        sa.Column("cvi_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "camera_id",
            sa.String(length=32),
            sa.ForeignKey("cameras.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("plate_text", sa.String(length=16), nullable=True),
        sa.Column("plate_confidence", sa.Float(), nullable=True),
        sa.Column("dominant_class", sa.String(length=20), nullable=True),
        sa.Column(
            "observations_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "idx_cvi_plate",
        "cvi_records",
        ["plate_text"],
        postgresql_where=sa.text("plate_text IS NOT NULL"),
    )
    op.create_index(
        "idx_cvi_last_seen",
        "cvi_records",
        [sa.text("last_seen DESC")],
    )

    # ------------------------------------------------------------ violations
    op.create_table(
        "violations",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "camera_id",
            sa.String(length=32),
            sa.ForeignKey("cameras.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "zone_id",
            sa.Integer(),
            sa.ForeignKey("zones.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "cvi_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("cvi_records.cvi_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("identity_key", sa.String(length=128), nullable=False),
        sa.Column(
            "cycle_id",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("plate_text", sa.String(length=16), nullable=True),
        sa.Column("plate_confidence", sa.Float(), nullable=True),
        sa.Column("plate_format", sa.String(length=16), nullable=True),
        sa.Column("plate_region_code", sa.String(length=4), nullable=True),
        sa.Column(
            "plate_valid_format",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
        sa.Column(
            "is_diplomatic",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
        sa.Column("vehicle_class", sa.String(length=20), nullable=True),
        sa.Column("first_seen_in_zone", sa.DateTime(timezone=True), nullable=False),
        sa.Column("violation_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exit_confirmed_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("snapshot_vehicle_url", sa.Text(), nullable=True),
        sa.Column("snapshot_plate_url", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("reviewed_by", sa.String(length=64), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("penalty_card_url", sa.Text(), nullable=True),
        sa.Column(
            "bbox",
            sa.dialects.postgresql.JSONB().with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint(
            "camera_id",
            "zone_id",
            "identity_key",
            "cycle_id",
            name="uniq_violation_identity_cycle",
        ),
    )
    op.create_index(
        "idx_violations_camera_time",
        "violations",
        ["camera_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_violations_plate",
        "violations",
        ["plate_text"],
        postgresql_where=sa.text("plate_text IS NOT NULL"),
    )
    op.create_index("idx_violations_status", "violations", ["status"])
    op.create_index(
        "idx_violations_region",
        "violations",
        ["plate_region_code"],
        postgresql_where=sa.text("plate_region_code IS NOT NULL"),
    )


def downgrade() -> None:
    for idx in (
        "idx_violations_region",
        "idx_violations_status",
        "idx_violations_plate",
        "idx_violations_camera_time",
    ):
        op.drop_index(idx, table_name="violations")
    op.drop_table("violations")

    for idx in ("idx_cvi_last_seen", "idx_cvi_plate"):
        op.drop_index(idx, table_name="cvi_records")
    op.drop_table("cvi_records")
