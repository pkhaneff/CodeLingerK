"""
ReviewSurface model - Tracks where reviews and summaries are posted on GitHub.
"""

from datetime import datetime
from uuid import uuid4
from sqlalchemy import DateTime, String, Integer, BigInteger
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from infra.database import Base


class ReviewSurface(Base):
    """
    ReviewSurface model - Tracks where reviews and summaries are posted on GitHub.

    Fields:
        id: UUID primary key
        repo: Repository full name (owner/repo)
        pr_number: Pull request number
        surface_type: Surface type (pr_body_summary, walkthrough_comment, pull_review)
        marker: Hidden HTML marker identifying this surface
        github_id: ID returned by GitHub API for the comment/review
        last_updated_sha: Head SHA when this surface was last updated
        created_at: Creation timestamp
        updated_at: Update timestamp
    """

    __tablename__ = 'review_surfaces'

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    repo: Mapped[str] = mapped_column(String(255), nullable=False)
    pr_number: Mapped[int] = mapped_column(Integer, nullable=False)
    surface_type: Mapped[str] = mapped_column(String(50), nullable=False)
    marker: Mapped[str | None] = mapped_column(String(100), nullable=True)
    github_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_updated_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    def __repr__(self) -> str:
        return f'<ReviewSurface {self.surface_type} for PR #{self.pr_number}>'
