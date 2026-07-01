"""
Code Graph models - Store parsed code structure in PostgreSQL.

Tables:
- indexed_files: Source files in indexed repositories
- symbols: Classes, functions, methods extracted from code
- symbol_calls: Function/method call relationships
- symbol_imports: Import statements
- symbol_inheritances: Class inheritance relationships
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    String,
    Integer,
    Boolean,
    DateTime,
    ForeignKey,
    Text,
    Index,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infra.database import Base


class IndexedFile(Base):
    """
    Represents a source file that has been indexed.

    Fields:
        id: UUID primary key
        repository_id: Foreign key to Repository
        path: Relative path within repository
        language: Programming language
        content_hash: Hash of file content for change detection
    """

    __tablename__ = 'indexed_files'

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
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    language: Mapped[str] = mapped_column(String(50), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(16), nullable=False)

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
    repository: Mapped['Repository'] = relationship('Repository')
    symbols: Mapped[list['Symbol']] = relationship(
        'Symbol',
        back_populates='file',
        cascade='all, delete-orphan',
    )
    imports: Mapped[list['SymbolImport']] = relationship(
        'SymbolImport',
        back_populates='file',
        cascade='all, delete-orphan',
    )

    __table_args__ = (
        UniqueConstraint('repository_id', 'path', name='uq_indexed_files_repo_path'),
        Index('idx_indexed_files_repository_id', 'repository_id'),
    )

    def __repr__(self) -> str:
        return f'<IndexedFile {self.path}>'


class Symbol(Base):
    """
    Represents a code symbol (class, function, or method).

    Fields:
        id: UUID primary key
        file_id: Foreign key to IndexedFile
        name: Symbol name
        symbol_type: 'class', 'function', or 'method'
        line_start: Starting line number
        line_end: Ending line number
        signature: Function/method signature
        docstring: Documentation string
        parent_class_name: For methods, the containing class name
        content_hash: Hash of symbol content
        decorators: List of decorator names
    """

    __tablename__ = 'symbols'

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    file_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey('indexed_files.id', ondelete='CASCADE'),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    symbol_type: Mapped[str] = mapped_column(String(20), nullable=False)
    line_start: Mapped[int] = mapped_column(Integer, nullable=False)
    line_end: Mapped[int] = mapped_column(Integer, nullable=True)
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    docstring: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_class_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(16), nullable=False)
    decorators: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
    )

    # Relationships
    file: Mapped['IndexedFile'] = relationship('IndexedFile', back_populates='symbols')

    # Calls made by this symbol (as caller)
    outgoing_calls: Mapped[list['SymbolCall']] = relationship(
        'SymbolCall',
        foreign_keys='SymbolCall.caller_id',
        back_populates='caller',
        cascade='all, delete-orphan',
    )

    # Calls received by this symbol (as callee)
    incoming_calls: Mapped[list['SymbolCall']] = relationship(
        'SymbolCall',
        foreign_keys='SymbolCall.callee_id',
        back_populates='callee',
        cascade='all, delete-orphan',
    )

    __table_args__ = (
        Index('idx_symbols_file_id', 'file_id'),
        Index('idx_symbols_name', 'name'),
        Index('idx_symbols_type', 'symbol_type'),
        Index('idx_symbols_parent_class', 'parent_class_name'),
    )

    def __repr__(self) -> str:
        return f'<Symbol {self.symbol_type}:{self.name}>'


class SymbolCall(Base):
    """
    Represents a function/method call relationship.

    Fields:
        id: UUID primary key
        caller_id: Symbol making the call (nullable for external callers)
        callee_id: Symbol being called (nullable for external callees)
        callee_name: Name of the callee (kept even for external symbols)
        line_number: Line where the call occurs
        is_external: True if callee is not in the repository
    """

    __tablename__ = 'symbol_calls'

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    caller_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey('symbols.id', ondelete='CASCADE'),
        nullable=True,
    )
    callee_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey('symbols.id', ondelete='SET NULL'),
        nullable=True,
    )
    callee_name: Mapped[str] = mapped_column(String(255), nullable=False)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    is_external: Mapped[bool] = mapped_column(Boolean, default=False)

    # Store repository_id for easier querying
    repository_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey('repositories.id', ondelete='CASCADE'),
        nullable=False,
    )

    # Relationships
    caller: Mapped['Symbol'] = relationship(
        'Symbol',
        foreign_keys=[caller_id],
        back_populates='outgoing_calls',
    )
    callee: Mapped['Symbol'] = relationship(
        'Symbol',
        foreign_keys=[callee_id],
        back_populates='incoming_calls',
    )

    __table_args__ = (
        Index('idx_symbol_calls_caller', 'caller_id'),
        Index('idx_symbol_calls_callee', 'callee_id'),
        Index('idx_symbol_calls_repository', 'repository_id'),
    )

    def __repr__(self) -> str:
        return f'<SymbolCall {self.caller_id} -> {self.callee_name}>'


class SymbolImport(Base):
    """
    Represents an import statement in a file.

    Fields:
        id: UUID primary key
        file_id: Foreign key to IndexedFile
        module: Module being imported
        imported_name: Specific name imported (for 'from X import Y')
        alias: Import alias (for 'import X as Y')
        is_relative: True for relative imports
        line_number: Line where import occurs
    """

    __tablename__ = 'symbol_imports'

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    file_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey('indexed_files.id', ondelete='CASCADE'),
        nullable=False,
    )
    module: Mapped[str] = mapped_column(String(255), nullable=False)
    imported_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    alias: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_relative: Mapped[bool] = mapped_column(Boolean, default=False)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # Relationships
    file: Mapped['IndexedFile'] = relationship('IndexedFile', back_populates='imports')

    __table_args__ = (
        Index('idx_symbol_imports_file', 'file_id'),
        Index('idx_symbol_imports_module', 'module'),
    )

    def __repr__(self) -> str:
        return f'<SymbolImport {self.module}:{self.imported_name}>'


class SymbolInheritance(Base):
    """
    Represents class inheritance relationship.

    Fields:
        id: UUID primary key
        child_class_id: Symbol ID of the child class
        parent_class_name: Name of the parent class
        parent_class_id: Symbol ID of parent (nullable for external classes)
        line_number: Line where inheritance is declared
        repository_id: Repository for easier querying
    """

    __tablename__ = 'symbol_inheritances'

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    child_class_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey('symbols.id', ondelete='CASCADE'),
        nullable=False,
    )
    parent_class_name: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_class_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey('symbols.id', ondelete='SET NULL'),
        nullable=True,
    )
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    repository_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey('repositories.id', ondelete='CASCADE'),
        nullable=False,
    )

    # Relationships
    child_class: Mapped['Symbol'] = relationship(
        'Symbol',
        foreign_keys=[child_class_id],
    )
    parent_class: Mapped['Symbol'] = relationship(
        'Symbol',
        foreign_keys=[parent_class_id],
    )

    __table_args__ = (
        Index('idx_symbol_inheritances_child', 'child_class_id'),
        Index('idx_symbol_inheritances_parent', 'parent_class_id'),
        Index('idx_symbol_inheritances_repository', 'repository_id'),
    )

    def __repr__(self) -> str:
        return f'<SymbolInheritance {self.child_class_id} extends {self.parent_class_name}>'
