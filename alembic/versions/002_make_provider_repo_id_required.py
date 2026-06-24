"""Make provider_repo_id required, github_id nullable.

Revision ID: 002_provider_repo_id_required
Revises: 001_gitlab_support
Create Date: 2024-01-02 00:00:00.000000

This migration:
- Makes provider_repo_id NOT NULL (required for all repos)
- Makes github_id nullable (not needed for GitLab repos)
- Adds unique constraint on (provider, provider_repo_id)
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '002_provider_repo_id_required'
down_revision: Union[str, None] = '001_gitlab_support'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Make provider_repo_id required, github_id nullable."""
    # First, backfill any NULL provider_repo_id values from github_id
    op.execute(
        """
        UPDATE repositories
        SET provider_repo_id = github_id
        WHERE provider_repo_id IS NULL AND github_id IS NOT NULL
        """
    )

    # Make provider_repo_id NOT NULL
    op.alter_column(
        'repositories',
        'provider_repo_id',
        existing_type=sa.BigInteger(),
        nullable=False,
    )

    # Make github_id nullable
    op.alter_column(
        'repositories',
        'github_id',
        existing_type=sa.BigInteger(),
        nullable=True,
    )

    # Drop the old non-unique index
    op.drop_index('idx_repositories_provider_repo', table_name='repositories')

    # Create unique index on (provider, provider_repo_id)
    op.create_index(
        'idx_repositories_provider_repo',
        'repositories',
        ['provider', 'provider_repo_id'],
        unique=True,
    )


def downgrade() -> None:
    """Revert changes."""
    # Drop unique index
    op.drop_index('idx_repositories_provider_repo', table_name='repositories')

    # Recreate non-unique index
    op.create_index(
        'idx_repositories_provider_repo',
        'repositories',
        ['provider', 'provider_repo_id'],
    )

    # Make github_id NOT NULL again (this may fail if there are NULL values)
    op.alter_column(
        'repositories',
        'github_id',
        existing_type=sa.BigInteger(),
        nullable=False,
    )

    # Make provider_repo_id nullable again
    op.alter_column(
        'repositories',
        'provider_repo_id',
        existing_type=sa.BigInteger(),
        nullable=True,
    )
