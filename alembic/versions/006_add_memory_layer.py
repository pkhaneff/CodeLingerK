"""006_add_memory_layer.py

Add Memory Layer tables:
    - repo_rules: Repository-scoped coding conventions for LLM prompt injection
    - ignored_patterns: Glob patterns to exclude files from review
    - accepted_decisions: Human-dismissed findings to prevent re-reporting

Revision ID: 006
Revises: 005_add_repository_is_active
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = '006'
down_revision = '7397facdb92c'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ─── repo_rules ────────────────────────────────────────────────────────────
    op.create_table(
        'repo_rules',
        sa.Column('id', postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            'repository_id',
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey('repositories.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('description', sa.Text, nullable=False),
        sa.Column('category', sa.String(100), nullable=True),
        sa.Column('is_active', sa.Boolean, default=True, nullable=False),
        sa.Column('created_by', sa.String(255), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index('idx_repo_rules_repository_id', 'repo_rules', ['repository_id'])
    op.create_index('idx_repo_rules_active', 'repo_rules', ['is_active'])

    # ─── ignored_patterns ──────────────────────────────────────────────────────
    op.create_table(
        'ignored_patterns',
        sa.Column('id', postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            'repository_id',
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey('repositories.id', ondelete='CASCADE'),
            nullable=True,  # NULL = global pattern
        ),
        sa.Column('pattern', sa.String(500), nullable=False),
        sa.Column('scope', sa.String(50), nullable=False, server_default='repository'),
        sa.Column('reason', sa.Text, nullable=True),
        sa.Column('is_active', sa.Boolean, default=True, nullable=False),
        sa.Column('created_by', sa.String(255), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index('idx_ignored_patterns_repo', 'ignored_patterns', ['repository_id'])
    op.create_index('idx_ignored_patterns_active', 'ignored_patterns', ['is_active'])

    # ─── accepted_decisions ────────────────────────────────────────────────────
    op.create_table(
        'accepted_decisions',
        sa.Column('id', postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            'repository_id',
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey('repositories.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('file_pattern', sa.String(500), nullable=True),
        sa.Column('rule_category', sa.String(100), nullable=True),
        sa.Column('finding_fingerprint', sa.String(64), nullable=True),
        sa.Column('original_comment', sa.Text, nullable=False),
        sa.Column('original_severity', sa.String(50), nullable=True),
        sa.Column('rationale', sa.Text, nullable=True),
        sa.Column('accepted_by', sa.String(255), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index('idx_accepted_decisions_repo', 'accepted_decisions', ['repository_id'])
    op.create_index('idx_accepted_decisions_fingerprint', 'accepted_decisions', ['finding_fingerprint'])

    # ─── Seed global ignored patterns ─────────────────────────────────────────
    op.execute("""
        INSERT INTO ignored_patterns (id, repository_id, pattern, scope, reason, is_active)
        VALUES
            (gen_random_uuid(), NULL, 'alembic/versions/*', 'global', 'Auto-generated migration files', true),
            (gen_random_uuid(), NULL, '**/__pycache__/**', 'global', 'Python bytecode cache', true),
            (gen_random_uuid(), NULL, '**/*.pb.go', 'global', 'Protobuf generated files', true),
            (gen_random_uuid(), NULL, '**/__generated__/**', 'global', 'Auto-generated code', true),
            (gen_random_uuid(), NULL, '**/*.min.js', 'global', 'Minified JavaScript', true),
            (gen_random_uuid(), NULL, '**/*.lock', 'global', 'Lock files (yarn.lock, poetry.lock etc)', true)
    """)


def downgrade() -> None:
    op.drop_table('accepted_decisions')
    op.drop_table('ignored_patterns')
    op.drop_table('repo_rules')
