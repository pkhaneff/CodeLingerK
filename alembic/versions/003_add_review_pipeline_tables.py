"""Add review pipeline tables.

Revision ID: 003_review_pipeline
Revises: 002_provider_repo_id
Create Date: 2024-01-15 00:00:00.000000

This migration adds the tables required for the AI review pipeline:
- pull_requests: Track PR metadata across snapshots
- snapshots: Immutable PR state at specific commits
- layers: Functional grouping of files
- layer_ranges: Line mappings within layers
- review_jobs: Queue job tracking

Also updates:
- reviews: Add snapshot_id, ai_passes, diagram_mermaid, review_order
- review_comments: Add provider_comment_id, is_synced, sync_error, confidence
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '003_review_pipeline'
down_revision: Union[str, None] = '002_provider_repo_id_required'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create review pipeline tables and update existing tables."""

    # ─────────────────────────────────────────────────────────────
    # 1. Create pull_requests table
    # ─────────────────────────────────────────────────────────────
    op.create_table(
        'pull_requests',
        sa.Column('id', UUID(as_uuid=False), primary_key=True),
        sa.Column(
            'repository_id',
            UUID(as_uuid=False),
            sa.ForeignKey('repositories.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('pr_number', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(500), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='open'),
        sa.Column('source_branch', sa.String(255), nullable=False),
        sa.Column('target_branch', sa.String(255), nullable=False),
        sa.Column('author', sa.String(255), nullable=True),
        sa.Column('html_url', sa.String(500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('repository_id', 'pr_number', name='uq_pull_requests_repo_pr'),
    )

    op.create_index('idx_pull_requests_repository_id', 'pull_requests', ['repository_id'])
    op.create_index('idx_pull_requests_status', 'pull_requests', ['status'])

    # ─────────────────────────────────────────────────────────────
    # 2. Create snapshots table
    # ─────────────────────────────────────────────────────────────
    op.create_table(
        'snapshots',
        sa.Column('id', UUID(as_uuid=False), primary_key=True),
        sa.Column(
            'pull_request_id',
            UUID(as_uuid=False),
            sa.ForeignKey('pull_requests.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('commit_sha', sa.String(40), nullable=False),
        sa.Column('diff_content', sa.Text(), nullable=True),
        sa.Column('files_changed', ARRAY(sa.String()), server_default='{}'),
        sa.Column('additions', sa.Integer(), server_default='0'),
        sa.Column('deletions', sa.Integer(), server_default='0'),
        sa.Column('status', sa.String(30), nullable=False, server_default='pending'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('context_token_count', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('pull_request_id', 'commit_sha', name='uq_snapshots_pr_commit'),
    )

    op.create_index('idx_snapshots_pull_request_id', 'snapshots', ['pull_request_id'])
    op.create_index('idx_snapshots_status', 'snapshots', ['status'])
    op.create_index('idx_snapshots_commit_sha', 'snapshots', ['commit_sha'])

    # ─────────────────────────────────────────────────────────────
    # 3. Create layers table
    # ─────────────────────────────────────────────────────────────
    op.create_table(
        'layers',
        sa.Column('id', UUID(as_uuid=False), primary_key=True),
        sa.Column(
            'snapshot_id',
            UUID(as_uuid=False),
            sa.ForeignKey('snapshots.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('layer_type', sa.String(30), nullable=False),
        sa.Column('label', sa.String(100), nullable=False),
        sa.Column('intent', sa.Text(), nullable=True),
        sa.Column('files', ARRAY(sa.String()), server_default='{}'),
        sa.Column('symbol_count', sa.Integer(), server_default='0'),
        sa.Column('risk_score', sa.Integer(), server_default='0'),
        sa.Column('review_order', sa.Integer(), server_default='50'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_index('idx_layers_snapshot_id', 'layers', ['snapshot_id'])
    op.create_index('idx_layers_type', 'layers', ['layer_type'])
    op.create_index('idx_layers_risk', 'layers', ['risk_score'])

    # ─────────────────────────────────────────────────────────────
    # 4. Create layer_ranges table
    # ─────────────────────────────────────────────────────────────
    op.create_table(
        'layer_ranges',
        sa.Column('id', UUID(as_uuid=False), primary_key=True),
        sa.Column(
            'layer_id',
            UUID(as_uuid=False),
            sa.ForeignKey('layers.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('file_path', sa.String(500), nullable=False),
        sa.Column('start_line', sa.Integer(), nullable=False),
        sa.Column('end_line', sa.Integer(), nullable=False),
        sa.Column('context_before', sa.Integer(), server_default='3'),
        sa.Column('context_after', sa.Integer(), server_default='3'),
        sa.Column('hunk_content', sa.Text(), nullable=True),
        sa.Column('symbols_in_range', ARRAY(sa.String()), nullable=True),
    )

    op.create_index('idx_layer_ranges_layer_id', 'layer_ranges', ['layer_id'])
    op.create_index('idx_layer_ranges_file', 'layer_ranges', ['file_path'])

    # ─────────────────────────────────────────────────────────────
    # 5. Create review_jobs table
    # ─────────────────────────────────────────────────────────────
    op.create_table(
        'review_jobs',
        sa.Column('id', UUID(as_uuid=False), primary_key=True),
        sa.Column(
            'snapshot_id',
            UUID(as_uuid=False),
            sa.ForeignKey('snapshots.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('job_type', sa.String(30), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='queued'),
        sa.Column('priority', sa.Integer(), server_default='50'),
        sa.Column('attempt', sa.Integer(), server_default='0'),
        sa.Column('max_attempts', sa.Integer(), server_default='3'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('result_data', JSONB(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('scheduled_for', sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index('idx_review_jobs_snapshot_id', 'review_jobs', ['snapshot_id'])
    op.create_index('idx_review_jobs_status', 'review_jobs', ['status'])
    op.create_index('idx_review_jobs_type_status', 'review_jobs', ['job_type', 'status'])
    op.create_index('idx_review_jobs_priority', 'review_jobs', ['priority'])
    op.create_index('idx_review_jobs_scheduled', 'review_jobs', ['scheduled_for'])

    # ─────────────────────────────────────────────────────────────
    # 6. Update reviews table
    # ─────────────────────────────────────────────────────────────
    op.add_column(
        'reviews',
        sa.Column(
            'snapshot_id',
            UUID(as_uuid=False),
            sa.ForeignKey('snapshots.id', ondelete='SET NULL'),
            nullable=True,
        ),
    )
    op.add_column('reviews', sa.Column('ai_passes', JSONB(), nullable=True))
    op.add_column('reviews', sa.Column('diagram_mermaid', sa.Text(), nullable=True))
    op.add_column('reviews', sa.Column('review_order', ARRAY(sa.String()), nullable=True))

    op.create_index('idx_reviews_snapshot_id', 'reviews', ['snapshot_id'])
    op.create_index('idx_reviews_repository_id', 'reviews', ['repository_id'])

    # ─────────────────────────────────────────────────────────────
    # 7. Update review_comments table
    # ─────────────────────────────────────────────────────────────
    op.add_column(
        'review_comments',
        sa.Column('provider_comment_id', sa.BigInteger(), nullable=True),
    )
    op.add_column(
        'review_comments',
        sa.Column('is_synced', sa.Boolean(), server_default='false'),
    )
    op.add_column(
        'review_comments',
        sa.Column('sync_error', sa.Text(), nullable=True),
    )
    op.add_column(
        'review_comments',
        sa.Column('confidence', sa.Float(), nullable=True),
    )

    op.create_index('idx_review_comments_review_id', 'review_comments', ['review_id'])
    op.create_index('idx_review_comments_provider_id', 'review_comments', ['provider_comment_id'])
    op.create_index('idx_review_comments_synced', 'review_comments', ['is_synced'])


def downgrade() -> None:
    """Remove review pipeline tables and columns."""

    # Drop indexes on review_comments
    op.drop_index('idx_review_comments_synced', table_name='review_comments')
    op.drop_index('idx_review_comments_provider_id', table_name='review_comments')
    op.drop_index('idx_review_comments_review_id', table_name='review_comments')

    # Drop columns from review_comments
    op.drop_column('review_comments', 'confidence')
    op.drop_column('review_comments', 'sync_error')
    op.drop_column('review_comments', 'is_synced')
    op.drop_column('review_comments', 'provider_comment_id')

    # Drop indexes on reviews
    op.drop_index('idx_reviews_repository_id', table_name='reviews')
    op.drop_index('idx_reviews_snapshot_id', table_name='reviews')

    # Drop columns from reviews
    op.drop_column('reviews', 'review_order')
    op.drop_column('reviews', 'diagram_mermaid')
    op.drop_column('reviews', 'ai_passes')
    op.drop_column('reviews', 'snapshot_id')

    # Drop review_jobs table
    op.drop_index('idx_review_jobs_scheduled', table_name='review_jobs')
    op.drop_index('idx_review_jobs_priority', table_name='review_jobs')
    op.drop_index('idx_review_jobs_type_status', table_name='review_jobs')
    op.drop_index('idx_review_jobs_status', table_name='review_jobs')
    op.drop_index('idx_review_jobs_snapshot_id', table_name='review_jobs')
    op.drop_table('review_jobs')

    # Drop layer_ranges table
    op.drop_index('idx_layer_ranges_file', table_name='layer_ranges')
    op.drop_index('idx_layer_ranges_layer_id', table_name='layer_ranges')
    op.drop_table('layer_ranges')

    # Drop layers table
    op.drop_index('idx_layers_risk', table_name='layers')
    op.drop_index('idx_layers_type', table_name='layers')
    op.drop_index('idx_layers_snapshot_id', table_name='layers')
    op.drop_table('layers')

    # Drop snapshots table
    op.drop_index('idx_snapshots_commit_sha', table_name='snapshots')
    op.drop_index('idx_snapshots_status', table_name='snapshots')
    op.drop_index('idx_snapshots_pull_request_id', table_name='snapshots')
    op.drop_table('snapshots')

    # Drop pull_requests table
    op.drop_index('idx_pull_requests_status', table_name='pull_requests')
    op.drop_index('idx_pull_requests_repository_id', table_name='pull_requests')
    op.drop_table('pull_requests')
