"""
BlacklistedToken model to invalidate logged-out and rotated tokens.
"""

from datetime import datetime
from uuid import uuid4
from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from infra.database import Base


class BlacklistedToken(Base):
    """
    Model for storing blacklisted tokens (revoked upon logout or refresh token rotation).

    Fields:
        id: UUID primary key
        token: Hashed or full JWT token value that is blacklisted
        blacklisted_at: Timestamp when token was blacklisted
        expires_at: Token's original expiration timestamp
        user_id: ID of the user who owned this token
    """

    __tablename__ = 'blacklisted_tokens'

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    token: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)
    blacklisted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey('users.id', ondelete='CASCADE'),
        nullable=False,
    )

    # Relationships
    user: Mapped['User'] = relationship('User')

    def __repr__(self) -> str:
        return f'<BlacklistedToken {self.token[:20]}... user={self.user_id}>'
