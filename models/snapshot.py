"""
Snapshot model - Immutable snapshot of PR state at a specific commit.

This is the foundation of the review pipeline. We never review HEAD directly;
instead, we create an immutable snapshot and process that.
"""

from datetime import datetime
from uuid import uuid4
import enum

from sqlalchemy import DateTime, String, Integer, ForeignKey, Text, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infra.database import Base


class SnapshotStatus(str, enum.Enum):
    """Snapshot processing status."""
    PENDING = 'pending'                     # Just created, waiting for processing
    CONTEXT_BUILDING = 'context_building'   # Extracting symbols and context
    LAYERING = 'layering'                   # Building functional layers
    REVIEWING = 'reviewing'                 # AI review in progress
    PUBLISHING = 'publishing'               # Posting to GitHub
    COMPLETED = 'completed'                 # Review done and posted
    FAILED = 'failed'                       # Processing failed


class Snapshot(Base):
    """
    Immutable snapshot of PR state at a specific commit.

    Never review HEAD directly - always work from snapshots.
    Each push to a PR creates a new snapshot.

    Fields:
        id: UUID primary key
        pull_request_id: FK to PullRequest
        commit_sha: The exact commit SHA (40 chars)
        diff_content: Full unified diff text (stored for replay)
        files_changed: List of file paths changed
        additions: Total lines added
        deletions: Total lines deleted
        status: Processing status
        error_message: If failed, the error
        context_token_count: Estimated tokens for AI context
        created_at: Snapshot creation time
        processed_at: When processing completed
    """

    __tablename__ = 'snapshots'

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    pull_request_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey('pull_requests.id', ondelete='CASCADE'),
        nullable=False,
    )
    commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)

    # Diff storage
    diff_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    files_changed: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        default=list,
        server_default='{}',
    )
    additions: Mapped[int] = mapped_column(Integer, default=0)
    deletions: Mapped[int] = mapped_column(Integer, default=0)

    # Processing state
    status: Mapped[str] = mapped_column(
        String(30),
        default=SnapshotStatus.PENDING.value,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    pull_request: Mapped['PullRequest'] = relationship(
        'PullRequest',
        back_populates='snapshots',
    )
    layers: Mapped[list['Layer']] = relationship(
        'Layer',
        back_populates='snapshot',
        cascade='all, delete-orphan',
    )
    review: Mapped['Review'] = relationship(
        'Review',
        back_populates='snapshot',
        uselist=False,
    )
    jobs: Mapped[list['ReviewJob']] = relationship(
        'ReviewJob',
        back_populates='snapshot',
        cascade='all, delete-orphan',
    )

    __table_args__ = (
        UniqueConstraint(
            'pull_request_id',
            'commit_sha',
            name='uq_snapshots_pr_commit',
        ),
        Index('idx_snapshots_pull_request_id', 'pull_request_id'),
        Index('idx_snapshots_status', 'status'),
        Index('idx_snapshots_commit_sha', 'commit_sha'),
    )

    def __repr__(self) -> str:
        return f'<Snapshot {self.commit_sha[:8]} ({self.status})>'

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            'id': self.id,
            'pull_request_id': self.pull_request_id,
            'commit_sha': self.commit_sha,
            'files_changed': self.files_changed,
            'additions': self.additions,
            'deletions': self.deletions,
            'status': self.status,
            'error_message': self.error_message,
            'context_token_count': self.context_token_count,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'processed_at': self.processed_at.isoformat() if self.processed_at else None,
        }

    @property
    def files_count(self) -> int:
        """Number of files changed in this snapshot."""
        return len(self.files_changed) if self.files_changed else 0

    @property
    def total_changes(self) -> int:
        """Total lines changed (additions + deletions)."""
        return self.additions + self.deletions

    @property
    def is_small_pr(self) -> bool:
        """Check if this is a small PR (for priority calculation)."""
        return self.files_count <= 5 and self.total_changes <= 100

    @property
    def is_processing(self) -> bool:
        """Check if snapshot is currently being processed."""
        return self.status in {
            SnapshotStatus.CONTEXT_BUILDING.value,
            SnapshotStatus.LAYERING.value,
            SnapshotStatus.REVIEWING.value,
            SnapshotStatus.PUBLISHING.value,
        }

    def mark_failed(self, error: str) -> None:
        """Mark snapshot as failed with error message."""
        self.status = SnapshotStatus.FAILED.value
        self.error_message = error

    def mark_completed(self) -> None:
        """Mark snapshot as completed."""
        self.status = SnapshotStatus.COMPLETED.value
        self.processed_at = datetime.utcnow()
