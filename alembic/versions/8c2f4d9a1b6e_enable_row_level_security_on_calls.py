"""enable row level security on calls

Revision ID: 8c2f4d9a1b6e
Revises: 76980d862c82
Create Date: 2026-10-21 07:15:56.119000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "8c2f4d9a1b6e"
down_revision: Union[str, Sequence[str], None] = "76980d862c82"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE calls ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE calls DISABLE ROW LEVEL SECURITY")
