"""
Layer and LayerRange models - Functional grouping for code changes.

Files are grouped into semantic layers (API, AUTH, DB, etc.) to enable
intent-based review rather than file-based review.
"""

from datetime import datetime
from uuid import uuid4
import enum

from sqlalchemy import DateTime, String, Integer, ForeignKey, Text, Index
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infra.database import Base


class LayerType(str, enum.Enum):
    """Functional layer types for code classification."""
    API = 'api'           # Routes, controllers, endpoints
    AUTH = 'auth'         # Authentication/authorization
    DB = 'db'             # Database, ORM, migrations
    SERVICE = 'service'   # Business logic services
    MODEL = 'model'       # Data models, schemas
    UTIL = 'util'         # Utilities, helpers
    CONFIG = 'config'     # Configuration
    TEST = 'test'         # Test files
    UI = 'ui'             # Frontend components
    INFRA = 'infra'       # Infrastructure, DevOps
    UNKNOWN = 'unknown'   # Cannot determine


class Layer(Base):
    """
    Functional layer grouping for files in a snapshot.

    Maps files to semantic purposes (API, AUTH, DB, etc.) to enable
    intent-based review. Each layer represents a cohesive set of changes
    with a unified purpose.

    Fields:
        id: UUID primary key
        snapshot_id: FK to Snapshot
        layer_type: Category (api, auth, db, service, etc.)
        label: Human-readable label
        intent: Brief description of layer's purpose in this PR
        files: List of file paths in this layer
        symbol_count: Number of impacted symbols
        risk_score: 0-100 risk assessment
        review_order: Suggested review order (lower = review first)
    """

    __tablename__ = 'layers'

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
    layer_type: Mapped[str] = mapped_column(String(30), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    intent: Mapped[str | None] = mapped_column(Text, nullable=True)
    files: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        default=list,
        server_default='{}',
    )
    symbol_count: Mapped[int] = mapped_column(Integer, default=0)
    risk_score: Mapped[int] = mapped_column(Integer, default=0)  # 0-100
    review_order: Mapped[int] = mapped_column(Integer, default=50)  # Lower = first

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
    )

    # Relationships
    snapshot: Mapped['Snapshot'] = relationship('Snapshot', back_populates='layers')
    ranges: Mapped[list['LayerRange']] = relationship(
        'LayerRange',
        back_populates='layer',
        cascade='all, delete-orphan',
    )

    __table_args__ = (
        Index('idx_layers_snapshot_id', 'snapshot_id'),
        Index('idx_layers_type', 'layer_type'),
        Index('idx_layers_risk', 'risk_score'),
    )

    def __repr__(self) -> str:
        return f'<Layer {self.layer_type}: {self.label}>'

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            'id': self.id,
            'snapshot_id': self.snapshot_id,
            'layer_type': self.layer_type,
            'label': self.label,
            'intent': self.intent,
            'files': self.files,
            'files_count': len(self.files) if self.files else 0,
            'symbol_count': self.symbol_count,
            'risk_score': self.risk_score,
            'review_order': self.review_order,
        }

    @property
    def files_count(self) -> int:
        """Number of files in this layer."""
        return len(self.files) if self.files else 0

    @property
    def is_high_risk(self) -> bool:
        """Check if this layer is high risk (>= 70)."""
        return self.risk_score >= 70

    @property
    def is_security_sensitive(self) -> bool:
        """Check if this layer contains security-sensitive code."""
        return self.layer_type in {LayerType.AUTH.value, LayerType.CONFIG.value}


class LayerRange(Base):
    """
    Maps diff hunks to layers with exact line ranges.

    Used for placing inline comments on the correct lines.
    Each range represents a contiguous block of code within a layer.

    Fields:
        id: UUID primary key
        layer_id: FK to Layer
        file_path: File this range belongs to
        start_line: Starting line number (in new file)
        end_line: Ending line number
        context_before: Lines of context before hunk
        context_after: Lines of context after hunk
        hunk_content: The actual diff hunk text
        symbols_in_range: Symbol names within this range
    """

    __tablename__ = 'layer_ranges'

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    layer_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey('layers.id', ondelete='CASCADE'),
        nullable=False,
    )
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    context_before: Mapped[int] = mapped_column(Integer, default=3)
    context_after: Mapped[int] = mapped_column(Integer, default=3)
    hunk_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    symbols_in_range: Mapped[list[str] | None] = mapped_column(
        ARRAY(String),
        nullable=True,
    )

    # Relationships
    layer: Mapped['Layer'] = relationship('Layer', back_populates='ranges')

    __table_args__ = (
        Index('idx_layer_ranges_layer_id', 'layer_id'),
        Index('idx_layer_ranges_file', 'file_path'),
    )

    def __repr__(self) -> str:
        return f'<LayerRange {self.file_path}:{self.start_line}-{self.end_line}>'

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            'id': self.id,
            'layer_id': self.layer_id,
            'file_path': self.file_path,
            'start_line': self.start_line,
            'end_line': self.end_line,
            'context_before': self.context_before,
            'context_after': self.context_after,
            'symbols_in_range': self.symbols_in_range,
        }

    @property
    def line_count(self) -> int:
        """Number of lines in this range."""
        return self.end_line - self.start_line + 1

    @property
    def expanded_start(self) -> int:
        """Start line including context."""
        return max(1, self.start_line - self.context_before)

    @property
    def expanded_end(self) -> int:
        """End line including context."""
        return self.end_line + self.context_after

    def contains_line(self, line: int) -> bool:
        """Check if a line number falls within this range."""
        return self.start_line <= line <= self.end_line

    def overlaps_with(self, start: int, end: int) -> bool:
        """Check if this range overlaps with another range."""
        return not (end < self.start_line or start > self.end_line)
