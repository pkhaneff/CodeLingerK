"""
ReviewRun model - Tracks each run of the review pipeline.
"""

from datetime import datetime
from uuid import uuid4
from sqlalchemy import DateTime, String, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from infra.database import Base


class ReviewRun(Base):
    """
    ReviewRun model - Tracks each run of the review pipeline.

    Fields:
        id: UUID primary key
        repo: Repository full name (owner/repo)
        pr_number: Pull request number
        mode: Review mode (full, incremental, refresh, etc.)
        base_sha: Base commit SHA
        head_sha: Head commit SHA
        status: Run status (pending, completed, failed)
        reviewed_files_count: Number of files reviewed in this run
        new_findings_count: Number of new findings posted
        duplicate_findings_count: Number of duplicate findings ignored
        resolved_findings_count: Number of previously seen findings resolved
        posted_comments_count: Number of inline comments posted to GitHub
        created_at: Creation timestamp
        completed_at: Completion timestamp
    """

    __tablename__ = 'review_runs'

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    repo: Mapped[str] = mapped_column(String(255), nullable=False)
    pr_number: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[str] = mapped_column(String(50), nullable=False)
    base_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    head_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)

    status: Mapped[str] = mapped_column(
        String(50),
        default='pending',
    )

    reviewed_files_count: Mapped[int] = mapped_column(Integer, default=0)
    new_findings_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_findings_count: Mapped[int] = mapped_column(Integer, default=0)
    resolved_findings_count: Mapped[int] = mapped_column(Integer, default=0)
    posted_comments_count: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def __repr__(self) -> str:
        return f'<ReviewRun {self.id[:8]} ({self.status})>'
