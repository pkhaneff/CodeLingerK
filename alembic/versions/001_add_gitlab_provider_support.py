"""Add GitLab provider support.

Revision ID: 001_gitlab_support
Revises:
Create Date: 2024-01-01 00:00:00.000000

This migration adds GitLab-specific fields to the users and repositories tables,
enabling multi-provider support while maintaining backward compatibility with
existing GitHub data.

Changes:
- users: Add gitlab_id, gitlab_username, gitlab_email, gitlab_avatar_url, gitlab_access_token
- repositories: Add provider column (default: 'github'), provider_repo_id
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '001_gitlab_support'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add GitLab provider support."""
    # Add GitLab fields to users table
    op.add_column(
        'users',
        sa.Column('gitlab_id', sa.BigInteger(), nullable=True, unique=True),
    )
    op.add_column(
        'users',
        sa.Column('gitlab_username', sa.String(255), nullable=True),
    )
    op.add_column(
        'users',
        sa.Column('gitlab_email', sa.String(255), nullable=True),
    )
    op.add_column(
        'users',
        sa.Column('gitlab_avatar_url', sa.String(500), nullable=True),
    )
    op.add_column(
        'users',
        sa.Column('gitlab_access_token', sa.String(500), nullable=True),
    )

    # Add provider column to repositories table
    # Default to 'github' for existing records
    op.add_column(
        'repositories',
        sa.Column(
            'provider',
            sa.String(50),
            nullable=False,
            server_default='github',
        ),
    )

    # Add provider_repo_id for future flexibility
    # This stores the provider-specific repository ID
    op.add_column(
        'repositories',
        sa.Column('provider_repo_id', sa.BigInteger(), nullable=True),
    )

    # Backfill provider_repo_id from github_id for existing records
    op.execute(
        """
        UPDATE repositories
        SET provider_repo_id = github_id
        WHERE github_id IS NOT NULL
        """
    )

    # Create index on provider column for efficient filtering
    op.create_index('idx_repositories_provider', 'repositories', ['provider'])

    # Create composite index for provider + provider_repo_id lookups
    op.create_index(
        'idx_repositories_provider_repo',
        'repositories',
        ['provider', 'provider_repo_id'],
    )


def downgrade() -> None:
    """Remove GitLab provider support."""
    # Drop indexes
    op.drop_index('idx_repositories_provider_repo', table_name='repositories')
    op.drop_index('idx_repositories_provider', table_name='repositories')

    # Remove columns from repositories
    op.drop_column('repositories', 'provider_repo_id')
    op.drop_column('repositories', 'provider')

    # Remove columns from users
    op.drop_column('users', 'gitlab_access_token')
    op.drop_column('users', 'gitlab_avatar_url')
    op.drop_column('users', 'gitlab_email')
    op.drop_column('users', 'gitlab_username')
    op.drop_column('users', 'gitlab_id')
