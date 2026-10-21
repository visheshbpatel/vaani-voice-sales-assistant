"""add recording digest for safe retries

Revision ID: c5a1d8e7f4b2
Revises: 8c2f4d9a1b6e
Create Date: 2026-10-21 10:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c5a1d8e7f4b2"
down_revision: str | Sequence[str] | None = "8c2f4d9a1b6e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("calls", sa.Column("recording_sha256", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("calls", "recording_sha256")
