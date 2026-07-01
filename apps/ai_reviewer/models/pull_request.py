"""
PullRequest model - Tracks PR metadata across multiple snapshots.
"""

from datetime import datetime
from uuid import uuid4
import enum

from sqlalchemy import DateTime, String, Integer, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infra.database import Base


class PullRequestStatus(str, enum.Enum):
    """PR lifecycle status."""
    OPEN = 'open'
    CLOSED = 'closed'
    MERGED = 'merged'


class PullRequest(Base):
    """
    PullRequest model for tracking PR metadata.

    A PR can have multiple snapshots (one per push/update).
    This model tracks the overall PR state while Snapshot tracks
    each immutable point-in-time state.

    Fields:
        id: UUID primary key
        repository_id: FK to Repository
        pr_number: GitHub PR number
        title: PR title
        status: open/closed/merged
        source_branch: Head branch name
        target_branch: Base branch name
        author: PR author username
        html_url: GitHub PR URL
        created_at: When PR was opened
        updated_at: Last update
    """

    __tablename__ = 'pull_requests'

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    repository_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey('repositories.id', ondelete='CASCADE'),
        nullable=False,
    )
    pr_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        default=PullRequestStatus.OPEN.value,
    )
    source_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    target_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    html_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    # Relationships
    repository: Mapped['Repository'] = relationship('Repository')
    snapshots: Mapped[list['Snapshot']] = relationship(
        'Snapshot',
        back_populates='pull_request',
        cascade='all, delete-orphan',
        order_by='desc(Snapshot.created_at)',
    )

    __table_args__ = (
        UniqueConstraint('repository_id', 'pr_number', name='uq_pull_requests_repo_pr'),
        Index('idx_pull_requests_repository_id', 'repository_id'),
        Index('idx_pull_requests_status', 'status'),
    )

    def __repr__(self) -> str:
        return f'<PullRequest #{self.pr_number} ({self.status})>'

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            'id': self.id,
            'repository_id': self.repository_id,
            'pr_number': self.pr_number,
            'title': self.title,
            'status': self.status,
            'source_branch': self.source_branch,
            'target_branch': self.target_branch,
            'author': self.author,
            'html_url': self.html_url,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    @property
    def latest_snapshot(self) -> 'Snapshot | None':
        """Get the most recent snapshot for this PR."""
        return self.snapshots[0] if self.snapshots else None
