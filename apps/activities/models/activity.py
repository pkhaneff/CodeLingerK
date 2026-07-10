"""
Activity model - Tracks user and repository activities on CodeLinger.
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, String, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infra.database import Base


class Activity(Base):
    """
    Activity model for tracking events like repo connected, webhook added,
    security scans, and pull request reviews.

    Fields:
        id: UUID primary key
        owner_id: Foreign key to User who owns the activity
        repo_id: Foreign key to Repository where activity occurred
        type: Type of activity (repo_connected, webhook_added, security_scan, pr_review)
        status: Status indicator (success, warning, error, info)
        title: Title of the activity
        description: Short description of the activity
        action_url: Navigation URL associated with this activity (optional)
        created_at: Creation timestamp
    """

    __tablename__ = 'activities'

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    owner_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey('users.id', ondelete='CASCADE'),
        nullable=False,
    )

    repo_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey('repositories.id', ondelete='CASCADE'),
        nullable=False,
    )

    type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(1000), nullable=False)
    action_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
    )

    # Relationships
    owner: Mapped['User'] = relationship('User')
    repository: Mapped['Repository'] = relationship('Repository')

    __table_args__ = (
        Index('idx_activities_owner_id', 'owner_id'),
        Index('idx_activities_repo_id', 'repo_id'),
        Index('idx_activities_created_at', 'created_at'),
    )

    def __repr__(self) -> str:
        return f'<Activity {self.type} - {self.status} ({self.id[:8]})>'

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            'id': self.id,
            'type': self.type,
            'status': self.status,
            'title': self.title,
            'description': self.description,
            'repo_name': self.repository.name if self.repository else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'action_url': self.action_url,
        }
