"""
Role model for Role-Based Access Control (RBAC).
"""

from uuid import uuid4
from sqlalchemy import String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from infra.database import Base


class Role(Base):
    """
    Role model for system authorization.

    Fields:
        id: UUID primary key
        authority: Unique character or string representing the permission level (e.g., '1' for Admin, '2' for User)
        name: Human-readable name of the role (e.g., 'Administrator', 'User')
    """

    __tablename__ = 'roles'

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    authority: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Relationships
    users: Mapped[list['User']] = relationship(
        'User',
        back_populates='role',
    )

    def __repr__(self) -> str:
        return f'<Role {self.name} ({self.authority})>'

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            'id': self.id,
            'authority': self.authority,
            'name': self.name,
        }
