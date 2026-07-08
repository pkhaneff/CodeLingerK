"""
Finding model - Represents each actionable code-level review comment.
"""

from datetime import datetime
from uuid import uuid4
from sqlalchemy import DateTime, String, Integer, BigInteger, Text, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from infra.database import Base


class Finding(Base):
    """
    Finding model - Represents each actionable code-level review comment.

    Fields:
        id: UUID primary key
        repo: Repository full name (owner/repo)
        pr_number: Pull request number
        signature: Unique signature hash to deduplicate comments across pushes
        file_path: Target file path
        line: Target line start
        category: Finding category (Security, Performance, etc.)
        severity: Finding severity (critical, major, minor, info)
        title: Short title of the finding
        evidence: Code evidence or context snippet
        impact: Short explanation of the impact / why this matters
        fix: Proposed code fix or suggestion
        first_seen_sha: Commit SHA where this finding was first seen
        last_seen_sha: Commit SHA where this finding was last seen
        status: State of the finding lifecycle (new, existing, resolved, duplicate, outdated, skipped)
        github_comment_id: The ID of the inline comment on GitHub
        created_at: Creation timestamp
        updated_at: Update timestamp
    """

    __tablename__ = 'findings'

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    repo: Mapped[str] = mapped_column(String(255), nullable=False)
    pr_number: Mapped[int] = mapped_column(Integer, nullable=False)
    signature: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    severity: Mapped[str | None] = mapped_column(String(50), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)

    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    impact: Mapped[str | None] = mapped_column(Text, nullable=True)
    fix: Mapped[str | None] = mapped_column(Text, nullable=True)

    first_seen_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_seen_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default='new')
    github_comment_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

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
        Index('idx_findings_repo_pr_sig', 'repo', 'pr_number', 'signature'),
    )

    def __repr__(self) -> str:
        return f'<Finding {self.file_path}:{self.line} [{self.status}]>'
