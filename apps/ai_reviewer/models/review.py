"""
Review and ReviewComment models - AI code review results.
"""

from datetime import datetime
from uuid import uuid4
import enum

from sqlalchemy import DateTime, String, Integer, BigInteger, ForeignKey, Text, Boolean, Float, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infra.database import Base


class ReviewStatus(str, enum.Enum):
    """Review processing status."""
    PENDING = 'pending'
    ANALYZING = 'analyzing'
    COMPLETED = 'completed'
    FAILED = 'failed'


class ReviewVerdict(str, enum.Enum):
    """Review verdict."""
    APPROVED = 'approved'
    CHANGES_REQUESTED = 'changes_requested'
    NEEDS_DISCUSSION = 'needs_discussion'


class CommentSeverity(str, enum.Enum):
    """Review comment severity."""
    INFO = 'info'
    WARNING = 'warning'
    ERROR = 'error'
    CRITICAL = 'critical'


class Review(Base):
    """
    Review model for AI code review results.

    Fields:
        id: UUID primary key
        repository_id: Foreign key to Repository
        pull_request_number: PR number (if PR review)
        commit_sha: Commit SHA being reviewed
        review_type: Type of review (push, pull_request)
        status: Review status
        verdict: Review verdict
        summary: AI-generated summary
        detailed_feedback: Structured feedback (JSON)
        changes_analyzed: Number of changes analyzed
        files_analyzed: Number of files analyzed
        ai_model: AI model used
        ai_tokens_used: Tokens consumed
        processing_time_ms: Processing time
        github_review_id: GitHub review ID (if posted)
    """

    __tablename__ = 'reviews'

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
    pull_request_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    review_type: Mapped[str] = mapped_column(String(50), nullable=False)

    status: Mapped[str] = mapped_column(
        String(50),
        default=ReviewStatus.PENDING.value,
    )
    verdict: Mapped[str | None] = mapped_column(String(50), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    detailed_feedback: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    changes_analyzed: Mapped[int] = mapped_column(Integer, default=0)
    files_analyzed: Mapped[int] = mapped_column(Integer, default=0)
    ai_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ai_tokens_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    processing_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    github_review_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Snapshot relationship (new field for pipeline)
    snapshot_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey('snapshots.id', ondelete='SET NULL'),
        nullable=True,
    )

    # AI multi-pass results
    ai_passes: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Structure: {
    #   "pass_1_understanding": {...},
    #   "pass_2_risks": {...},
    #   "pass_3_quality": {...},
    #   "pass_4_business": {...},
    #   "pass_5_comments": [...]
    # }

    # Generated diagram (mermaid syntax)
    diagram_mermaid: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Suggested review order (file paths)
    review_order: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    repository: Mapped['Repository'] = relationship('Repository', back_populates='reviews')
    snapshot: Mapped['Snapshot'] = relationship('Snapshot', back_populates='review')
    comments: Mapped[list['ReviewComment']] = relationship(
        'ReviewComment',
        back_populates='review',
        cascade='all, delete-orphan',
    )

    __table_args__ = (
        Index('idx_reviews_snapshot_id', 'snapshot_id'),
        Index('idx_reviews_repository_id', 'repository_id'),
    )

    def __repr__(self) -> str:
        return f'<Review {self.id[:8]} ({self.status})>'

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            'id': self.id,
            'repository_id': self.repository_id,
            'snapshot_id': self.snapshot_id,
            'pull_request_number': self.pull_request_number,
            'commit_sha': self.commit_sha,
            'review_type': self.review_type,
            'status': self.status,
            'verdict': self.verdict,
            'summary': self.summary,
            'changes_analyzed': self.changes_analyzed,
            'files_analyzed': self.files_analyzed,
            'ai_model': self.ai_model,
            'ai_tokens_used': self.ai_tokens_used,
            'processing_time_ms': self.processing_time_ms,
            'review_order': self.review_order,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
        }


class ReviewComment(Base):
    """
    Review comment model for individual file/line comments.

    Fields:
        id: UUID primary key
        review_id: Foreign key to Review
        file_path: File being commented on
        line_start: Starting line number
        line_end: Ending line number
        symbol_name: Symbol name (function/class)
        symbol_type: Symbol type
        severity: Comment severity
        category: Comment category (security, performance, etc.)
        comment: Comment text
        suggestion: Suggested fix
        github_comment_id: GitHub comment ID (if posted)
    """

    __tablename__ = 'review_comments'

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    review_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey('reviews.id', ondelete='CASCADE'),
        nullable=False,
    )

    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    line_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    line_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    symbol_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    symbol_type: Mapped[str | None] = mapped_column(String(50), nullable=True)

    severity: Mapped[str] = mapped_column(
        String(50),
        default=CommentSeverity.INFO.value,
    )
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    comment: Mapped[str] = mapped_column(Text, nullable=False)
    suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)

    github_comment_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Sync tracking (for GitHub integration)
    provider_comment_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    is_synced: Mapped[bool] = mapped_column(Boolean, default=False)
    sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # AI confidence score (0.0 - 1.0)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
    )

    # Relationships
    review: Mapped['Review'] = relationship('Review', back_populates='comments')

    __table_args__ = (
        Index('idx_review_comments_review_id', 'review_id'),
        Index('idx_review_comments_provider_id', 'provider_comment_id'),
        Index('idx_review_comments_synced', 'is_synced'),
    )

    def __repr__(self) -> str:
        return f'<ReviewComment {self.file_path}:{self.line_start}>'

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            'id': self.id,
            'file_path': self.file_path,
            'line_start': self.line_start,
            'line_end': self.line_end,
            'symbol_name': self.symbol_name,
            'symbol_type': self.symbol_type,
            'severity': self.severity,
            'category': self.category,
            'comment': self.comment,
            'suggestion': self.suggestion,
            'confidence': self.confidence,
            'is_synced': self.is_synced,
        }
