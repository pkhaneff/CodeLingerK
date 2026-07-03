"""Add pipeline_failures column to reviews table

Tracks how many AI review passes failed during a review run.
- 0 = all passes succeeded (clean, reliable review)
- 1-2 = partial failure; verdict is conservative (needs_discussion instead of approved)
- The UI can use this field to display a warning: "Review had partial failures"

Revision ID: 007_add_pipeline_failures
Revises: 006
Create Date: 2026-07-03
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '007_add_pipeline_failures'
down_revision: Union[str, None] = '006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add pipeline_failures column to reviews table."""
    op.add_column(
        'reviews',
        sa.Column(
            'pipeline_failures',
            sa.Integer(),
            nullable=False,
            server_default='0',
            comment=(
                'Number of AI passes that returned error data. '
                '0=clean, 1-2=partial failure with conservative verdict.'
            ),
        ),
    )


def downgrade() -> None:
    """Remove pipeline_failures column from reviews table."""
    op.drop_column('reviews', 'pipeline_failures')
