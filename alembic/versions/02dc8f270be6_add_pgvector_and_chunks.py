"""add_pgvector_and_chunks

Revision ID: 02dc8f270be6
Revises: 007_add_pipeline_failures
Create Date: 2026-07-03 16:24:00.651268

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision: str = '02dc8f270be6'
down_revision: Union[str, Sequence[str], None] = '007_add_pipeline_failures'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # 2. Add embedding column to symbols table
    op.add_column('symbols', sa.Column('embedding', Vector(1536), nullable=True))

    # 3. Create file_chunks table
    op.create_table(
        'file_chunks',
        sa.Column('id', postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column('file_id', postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column('chunk_index', sa.Integer(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('embedding', Vector(1536), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['file_id'], ['indexed_files.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_file_chunks_file_id', 'file_chunks', ['file_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    # 1. Drop file_chunks table
    op.drop_index('idx_file_chunks_file_id', table_name='file_chunks')
    op.drop_table('file_chunks')

    # 2. Remove embedding column from symbols
    op.drop_column('symbols', 'embedding')
