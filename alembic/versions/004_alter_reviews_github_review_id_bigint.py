"""Alter reviews.github_review_id to BIGINT

Revision ID: 004_reviews_github_review_bigint
Revises: 003_review_pipeline
Create Date: 2026-06-24 00:00:00.000000

Change the `github_review_id` column on `reviews` from INTEGER to BIGINT
so it can store large provider IDs returned by GitHub/GitHub Enterprise.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '004_reviews_github_review_bigint'
down_revision: Union[str, None] = '003_review_pipeline'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Alter `reviews.github_review_id` to BIGINT."""
    # Existing column was INT; switch to BIGINT to accept larger provider IDs.
    op.alter_column(
        'reviews',
        'github_review_id',
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=True,
    )


def downgrade() -> None:
    """Revert `reviews.github_review_id` back to INTEGER."""
    op.alter_column(
        'reviews',
        'github_review_id',
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=True,
    )
