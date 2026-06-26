"""Add is_active column to repositories

Revision ID: 005_repository_is_active
Revises: 004_reviews_github_review_bigint
Create Date: 2026-06-26 00:00:00.000000

Add `is_active` column to repositories table to track which repository
is currently active for a user. Only one repository can be active per user.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '005_repository_is_active'
down_revision: Union[str, None] = '004_reviews_github_review_bigint'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add is_active column to repositories table."""
    op.add_column(
        'repositories',
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='false'),
    )


def downgrade() -> None:
    """Remove is_active column from repositories table."""
    op.drop_column('repositories', 'is_active')
