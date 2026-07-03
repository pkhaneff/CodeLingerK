"""add_is_embedded_columns

Revision ID: d859c6bb84d6
Revises: 02dc8f270be6
Create Date: 2026-07-03 18:13:44.420434

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd859c6bb84d6'
down_revision: Union[str, Sequence[str], None] = '02dc8f270be6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('symbols', sa.Column('is_embedded', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('file_chunks', sa.Column('is_embedded', sa.Boolean(), server_default=sa.text('false'), nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('file_chunks', 'is_embedded')
    op.drop_column('symbols', 'is_embedded')
