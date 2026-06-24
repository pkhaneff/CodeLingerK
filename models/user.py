"""
User model - GitHub OAuth users.
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, String, BigInteger
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infra.database import Base


class User(Base):
    """
    User model for GitHub OAuth authenticated users.

    Fields:
        id: UUID primary key
        github_id: GitHub user ID (unique)
        github_username: GitHub login name
        github_email: GitHub email (optional)
        github_avatar_url: GitHub avatar URL
        github_access_token: Encrypted OAuth access token
        created_at: Account creation timestamp
        updated_at: Last update timestamp
        last_login_at: Last login timestamp
        is_active: Whether user account is active
    """

    __tablename__ = 'users'

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    github_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    github_username: Mapped[str] = mapped_column(String(255), nullable=False)
    github_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    github_avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    github_access_token: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationships
    repositories: Mapped[list['Repository']] = relationship(
        'Repository',
        back_populates='owner',
        cascade='all, delete-orphan',
    )

    def __repr__(self) -> str:
        return f'<User {self.github_username} ({self.github_id})>'

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            'id': self.id,
            'github_id': self.github_id,
            'github_username': self.github_username,
            'github_email': self.github_email,
            'github_avatar_url': self.github_avatar_url,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'last_login_at': self.last_login_at.isoformat() if self.last_login_at else None,
        }
