"""
ReviewJob model - Tracks background job execution for review pipeline.

Jobs progress through the pipeline: snapshot → context → layer → review → publish
"""

from datetime import datetime
from uuid import uuid4
import enum

from sqlalchemy import DateTime, String, Integer, ForeignKey, Text, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infra.database import Base


class JobStatus(str, enum.Enum):
    """Job execution status."""
    QUEUED = 'queued'           # In queue, waiting
    PROCESSING = 'processing'   # Currently being processed
    COMPLETED = 'completed'     # Successfully completed
    FAILED = 'failed'           # Failed, may retry
    RETRYING = 'retrying'       # Scheduled for retry
    DEAD = 'dead'               # Max retries exceeded, moved to DLQ


class JobType(str, enum.Enum):
    """Pipeline job types."""
    SNAPSHOT = 'snapshot'       # Create snapshot from webhook
    CONTEXT = 'context'         # Build context (extract symbols)
    LAYER = 'layer'             # Build functional layers
    REVIEW = 'review'           # AI review
    PUBLISH = 'publish'         # Post to GitHub


class ReviewJob(Base):
    """
    Tracks background job execution for review pipeline.

    Each snapshot goes through multiple jobs in sequence:
    CONTEXT → LAYER → REVIEW → PUBLISH

    Jobs support:
    - Priority ordering (small PRs processed first)
    - Exponential backoff retry
    - Dead letter queue for failed jobs
    - Result storage for debugging

    Fields:
        id: UUID primary key
        snapshot_id: FK to Snapshot being processed
        job_type: Which phase (snapshot, context, layer, review, publish)
        status: Current status
        priority: Higher = process first (small PRs get high priority)
        attempt: Current attempt number
        max_attempts: Maximum retry count
        error_message: Last error if failed
        result_data: JSON result on completion
        started_at: When processing began
        completed_at: When finished
        scheduled_for: Delayed execution time (for retries)
    """

    __tablename__ = 'review_jobs'

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    snapshot_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey('snapshots.id', ondelete='CASCADE'),
        nullable=False,
    )
    job_type: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        default=JobStatus.QUEUED.value,
    )

    # Priority and retry
    priority: Mapped[int] = mapped_column(Integer, default=50)  # 0-100, higher = first
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)

    # Error tracking
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    scheduled_for: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    snapshot: Mapped['Snapshot'] = relationship('Snapshot', back_populates='jobs')

    __table_args__ = (
        Index('idx_review_jobs_snapshot_id', 'snapshot_id'),
        Index('idx_review_jobs_status', 'status'),
        Index('idx_review_jobs_type_status', 'job_type', 'status'),
        Index('idx_review_jobs_priority', 'priority'),
        Index('idx_review_jobs_scheduled', 'scheduled_for'),
    )

    def __repr__(self) -> str:
        return f'<ReviewJob {self.job_type} ({self.status})>'

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            'id': self.id,
            'snapshot_id': self.snapshot_id,
            'job_type': self.job_type,
            'status': self.status,
            'priority': self.priority,
            'attempt': self.attempt,
            'max_attempts': self.max_attempts,
            'error_message': self.error_message,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'scheduled_for': self.scheduled_for.isoformat() if self.scheduled_for else None,
        }

    @property
    def can_retry(self) -> bool:
        """Check if job can be retried."""
        return self.attempt < self.max_attempts

    @property
    def is_terminal(self) -> bool:
        """Check if job is in a terminal state."""
        return self.status in {JobStatus.COMPLETED.value, JobStatus.DEAD.value}

    @property
    def duration_ms(self) -> int | None:
        """Get processing duration in milliseconds."""
        if self.started_at and self.completed_at:
            delta = self.completed_at - self.started_at
            return int(delta.total_seconds() * 1000)
        return None

    def start_processing(self) -> None:
        """Mark job as started."""
        self.status = JobStatus.PROCESSING.value
        self.started_at = datetime.utcnow()
        self.attempt += 1

    def complete(self, result: dict | None = None) -> None:
        """Mark job as completed."""
        self.status = JobStatus.COMPLETED.value
        self.completed_at = datetime.utcnow()
        self.result_data = result

    def fail(self, error: str) -> None:
        """Mark job as failed."""
        self.status = JobStatus.FAILED.value
        self.error_message = error
        self.completed_at = datetime.utcnow()

    def mark_for_retry(self, delay_seconds: int) -> None:
        """Schedule job for retry with delay."""
        from datetime import timedelta
        self.status = JobStatus.RETRYING.value
        self.scheduled_for = datetime.utcnow() + timedelta(seconds=delay_seconds)

    def mark_dead(self) -> None:
        """Mark job as dead (max retries exceeded)."""
        self.status = JobStatus.DEAD.value
        self.completed_at = datetime.utcnow()

    @staticmethod
    def calculate_backoff(attempt: int) -> int:
        """
        Calculate exponential backoff delay.

        Formula: min(30 * 2^attempt, 1800) seconds
        Attempt 1: 60s
        Attempt 2: 120s
        Attempt 3: 240s
        Max: 1800s (30 minutes)
        """
        return min(30 * (2 ** attempt), 1800)

    @staticmethod
    def calculate_priority(files_count: int, total_changes: int) -> int:
        """
        Calculate job priority based on PR size.

        Small PRs get high priority for fast feedback.
        Priority scale: 0-100 (higher = process first)
        """
        if files_count <= 3 and total_changes <= 50:
            return 95  # Tiny PR - immediate
        elif files_count <= 5 and total_changes <= 100:
            return 80  # Small PR - high priority
        elif files_count <= 10 and total_changes <= 300:
            return 60  # Medium PR - normal
        elif files_count <= 20 and total_changes <= 500:
            return 40  # Large PR - lower priority
        else:
            return 20  # Huge PR - lowest priority
