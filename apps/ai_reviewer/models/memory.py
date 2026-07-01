"""
Memory models - Repository-scoped rules, patterns and accepted decisions.

Memory allows the system to learn from human reviewer feedback and
avoid surfacing the same issues repeatedly.

Tables:
    repo_rules:         Per-repo review rules ("always check for X")
    ignored_patterns:   File/path patterns to skip in review
    accepted_decisions: Human-accepted findings (suppress re-reporting)
"""

from datetime import datetime
from uuid import uuid4
import enum

from sqlalchemy import DateTime, String, Integer, ForeignKey, Text, Boolean, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infra.database import Base


class IgnoredPatternScope(str, enum.Enum):
    """Scope for ignored file patterns."""
    GLOBAL = 'global'           # All repositories
    REPOSITORY = 'repository'   # Specific repository only


class RepoRule(Base):
    """
    Repository-scoped review rule.

    These rules are injected into the AI prompt context to help the LLM
    understand project-specific conventions.

    Examples:
        - "This project uses optimistic locking, not pessimistic locking"
        - "All service methods must have explicit transaction boundaries"
        - "Never use datetime.now(), always use datetime.utcnow()"
    """

    __tablename__ = 'repo_rules'

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
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Who created/updated this rule
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
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
        Index('idx_repo_rules_repository_id', 'repository_id'),
        Index('idx_repo_rules_active', 'is_active'),
    )

    def __repr__(self) -> str:
        return f'<RepoRule {self.title!r}>'

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'repository_id': self.repository_id,
            'title': self.title,
            'description': self.description,
            'category': self.category,
            'is_active': self.is_active,
            'created_by': self.created_by,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class IgnoredPattern(Base):
    """
    File/path pattern to exclude from AI review.

    The pattern is matched against changed file paths using fnmatch-style glob.

    Examples:
        - "migrations/**" — skip all migration files
        - "**/__generated__/**" — skip auto-generated code
        - "*.pb.go" — skip protobuf generated files
        - "tests/fixtures/**" — skip test fixture files
    """

    __tablename__ = 'ignored_patterns'

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    # NULL repository_id means global (applies to all repos)
    repository_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey('repositories.id', ondelete='CASCADE'),
        nullable=True,
    )
    pattern: Mapped[str] = mapped_column(String(500), nullable=False)
    scope: Mapped[str] = mapped_column(
        String(50),
        default=IgnoredPatternScope.REPOSITORY.value,
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
    )

    __table_args__ = (
        Index('idx_ignored_patterns_repo', 'repository_id'),
        Index('idx_ignored_patterns_active', 'is_active'),
    )

    def __repr__(self) -> str:
        return f'<IgnoredPattern {self.pattern!r}>'

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'repository_id': self.repository_id,
            'pattern': self.pattern,
            'scope': self.scope,
            'reason': self.reason,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class AcceptedDecision(Base):
    """
    A human reviewer's decision to accept (suppress) a specific finding.

    When a human reviewer dismisses an AI comment as "not applicable",
    that decision is stored here. Future reviews on the same repo will
    not re-surface findings that are structurally similar to accepted ones.

    Matching is done on (repository_id, file_pattern, finding_fingerprint).
    The fingerprint is a normalized hash of the issue type + location.
    """

    __tablename__ = 'accepted_decisions'

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

    # What was accepted
    file_pattern: Mapped[str | None] = mapped_column(String(500), nullable=True)
    rule_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Stable fingerprint of the finding (category + message hash)
    finding_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Original finding details (for auditing)
    original_comment: Mapped[str] = mapped_column(Text, nullable=False)
    original_severity: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Reason for acceptance
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    accepted_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # TTL: after this date the acceptance expires and the issue may resurface
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
    )

    __table_args__ = (
        Index('idx_accepted_decisions_repo', 'repository_id'),
        Index('idx_accepted_decisions_fingerprint', 'finding_fingerprint'),
    )

    def __repr__(self) -> str:
        return f'<AcceptedDecision {self.finding_fingerprint!r}>'

    @property
    def is_expired(self) -> bool:
        """Return True if this acceptance has expired."""
        if self.expires_at is None:
            return False
        return datetime.utcnow() > self.expires_at

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'repository_id': self.repository_id,
            'file_pattern': self.file_pattern,
            'rule_category': self.rule_category,
            'finding_fingerprint': self.finding_fingerprint,
            'original_severity': self.original_severity,
            'rationale': self.rationale,
            'accepted_by': self.accepted_by,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
