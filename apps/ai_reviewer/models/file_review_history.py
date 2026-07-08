"""
FileReviewHistory model - Tracks file review status across push events.
"""

from datetime import datetime
from uuid import uuid4
from sqlalchemy import DateTime, String, Integer, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from infra.database import Base


class FileReviewHistory(Base):
    """
    FileReviewHistory model - Tracks file review status across push events.

    Fields:
        id: UUID primary key
        repo: Repository full name (owner/repo)
        pr_number: Pull request number
        file_path: Path of the reviewed file
        first_seen_sha: Commit SHA where this file was first seen in the PR
        last_seen_sha: Commit SHA where this file was last updated/reviewed
        review_status: File result label (e.g. reviewed, no new issues found, skipped: lockfile, etc.)
        findings_count: Number of findings found on this file
        skipped_reason: Reason if file was skipped during review
        created_at: Creation timestamp
        updated_at: Update timestamp
    """

    __tablename__ = 'file_review_history'

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    repo: Mapped[str] = mapped_column(String(255), nullable=False)
    pr_number: Mapped[int] = mapped_column(Integer, nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)

    first_seen_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_seen_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    review_status: Mapped[str] = mapped_column(String(100), nullable=False)
    findings_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    __table_args__ = (
        UniqueConstraint('repo', 'pr_number', 'file_path', name='uq_file_review_history_repo_pr_file'),
        Index('idx_file_review_history_repo_pr', 'repo', 'pr_number'),
    )

    def __repr__(self) -> str:
        return f'<FileReviewHistory {self.file_path} [{self.review_status}]>'
