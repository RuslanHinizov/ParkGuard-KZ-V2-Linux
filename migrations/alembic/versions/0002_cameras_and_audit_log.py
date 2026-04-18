"""cameras + audit_log tables

Revision ID: 0002_cameras_audit
Revises: 0001_postgis
Create Date: 2026-04-18

Spec §10.5 — cameras is the root of the FK chain (zones, cvi_records,
violations all reference it). audit_log is standalone; every mutating
endpoint writes here via services.audit.record_audit().
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_cameras_audit"
down_revision: str | None = "0001_postgis"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    # ---- cameras ---------------------------------------------------------
    op.create_table(
        "cameras",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("rtsp_substream_url", sa.Text(), nullable=False),
        sa.Column("rtsp_mainstream_url", sa.Text(), nullable=True),
        sa.Column("location_description", sa.Text(), nullable=True),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("TRUE"),
        ),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
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

    # ---- audit_log -------------------------------------------------------
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("operator_name", sa.String(length=64), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=True),
        sa.Column("target_id", sa.String(length=64), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "idx_audit_operator",
        "audit_log",
        ["operator_name", sa.text("created_at DESC")],
    )
    op.create_index("idx_audit_target", "audit_log", ["target_type", "target_id"])


def downgrade() -> None:
    op.drop_index("idx_audit_target", table_name="audit_log")
    op.drop_index("idx_audit_operator", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_table("cameras")
