"""
User model - Multi-provider OAuth users.

Supports GitHub and GitLab authentication.
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, String, BigInteger
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infra.database import Base


class User(Base):
    """
    User model for OAuth authenticated users.

    Supports multiple Git providers (GitHub, GitLab).
    Each provider has its own set of fields for user identification.

    Fields:
        id: UUID primary key
        # GitHub fields
        github_id: GitHub user ID (unique)
        github_username: GitHub login name
        github_email: GitHub email (optional)
        github_avatar_url: GitHub avatar URL
        github_access_token: GitHub OAuth access token
        # GitLab fields
        gitlab_id: GitLab user ID (unique)
        gitlab_username: GitLab username
        gitlab_email: GitLab email (optional)
        gitlab_avatar_url: GitLab avatar URL
        gitlab_access_token: GitLab OAuth access token
        # Timestamps
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

    # GitHub OAuth fields
    github_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    github_username: Mapped[str] = mapped_column(String(255), nullable=False)
    github_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    github_avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    github_access_token: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # GitLab OAuth fields
    gitlab_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True)
    gitlab_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    gitlab_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    gitlab_avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    gitlab_access_token: Mapped[str | None] = mapped_column(String(500), nullable=True)

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
        username = self.github_username or self.gitlab_username or 'unknown'
        provider_id = self.github_id or self.gitlab_id or 'N/A'
        return f'<User {username} ({provider_id})>'

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            'id': self.id,
            # GitHub fields
            'github_id': self.github_id,
            'github_username': self.github_username,
            'github_email': self.github_email,
            'github_avatar_url': self.github_avatar_url,
            # GitLab fields
            'gitlab_id': self.gitlab_id,
            'gitlab_username': self.gitlab_username,
            'gitlab_email': self.gitlab_email,
            'gitlab_avatar_url': self.gitlab_avatar_url,
            # Timestamps
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'last_login_at': self.last_login_at.isoformat() if self.last_login_at else None,
        }

    def get_access_token(self, provider: str) -> str | None:
        """Get access token for specified provider."""
        if provider == 'github':
            return self.github_access_token
        elif provider == 'gitlab':
            return self.gitlab_access_token
        return None

    def has_provider(self, provider: str) -> bool:
        """Check if user is connected to specified provider."""
        if provider == 'github':
            return self.github_id is not None
        elif provider == 'gitlab':
            return self.gitlab_id is not None
        return False
