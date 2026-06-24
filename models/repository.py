"""
Repository model - GitHub repositories added for indexing.
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, String, BigInteger, ForeignKey, Enum
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
import enum

from infra.database import Base


class IndexStatus(str, enum.Enum):
    """Repository indexing status."""
    PENDING = 'pending'
    INDEXING = 'indexing'
    INDEXED = 'indexed'
    FAILED = 'failed'


class Repository(Base):
    """
    Repository model for GitHub repositories.

    Fields:
        id: UUID primary key
        github_id: GitHub repository ID (unique)
        owner_id: Foreign key to User
        full_name: Full repo name (owner/repo)
        name: Repository name
        clone_url: Git clone URL
        default_branch: Default branch name
        is_indexed: Whether repo has been indexed
        last_indexed_at: Last indexing timestamp
        last_indexed_commit: Last indexed commit SHA
        index_status: Current indexing status
        webhook_id: GitHub webhook ID
        webhook_secret: Webhook secret for verification
        settings: Repository-specific settings (JSON)
    """

    __tablename__ = 'repositories'

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    github_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    owner_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey('users.id', ondelete='CASCADE'),
        nullable=False,
    )
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    clone_url: Mapped[str] = mapped_column(String(500), nullable=False)
    default_branch: Mapped[str] = mapped_column(String(255), default='main')

    is_indexed: Mapped[bool] = mapped_column(Boolean, default=False)
    last_indexed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_indexed_commit: Mapped[str | None] = mapped_column(String(40), nullable=True)
    index_status: Mapped[str] = mapped_column(
        String(50),
        default=IndexStatus.PENDING.value,
    )

    webhook_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    webhook_secret: Mapped[str | None] = mapped_column(String(255), nullable=True)

    settings: Mapped[dict] = mapped_column(JSONB, default=dict)

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
    owner: Mapped['User'] = relationship('User', back_populates='repositories')
    reviews: Mapped[list['Review']] = relationship(
        'Review',
        back_populates='repository',
        cascade='all, delete-orphan',
    )

    def __repr__(self) -> str:
        return f'<Repository {self.full_name}>'

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            'id': self.id,
            'github_id': self.github_id,
            'full_name': self.full_name,
            'name': self.name,
            'default_branch': self.default_branch,
            'is_indexed': self.is_indexed,
            'index_status': self.index_status,
            'last_indexed_at': self.last_indexed_at.isoformat() if self.last_indexed_at else None,
            'last_indexed_commit': self.last_indexed_commit,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
