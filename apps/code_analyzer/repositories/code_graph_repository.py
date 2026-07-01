"""
Code Graph Repository - Data access layer for code graph operations.

This repository handles all database operations for:
- IndexedFile
- Symbol
- SymbolCall
- SymbolImport
- SymbolInheritance

Note: This repository does NOT commit transactions.
Transaction control is handled by the service layer.
"""

from sqlalchemy import select, delete, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from apps.code_analyzer.models.code_graph import (
    IndexedFile,
    Symbol,
    SymbolCall,
    SymbolImport,
    SymbolInheritance,
)
from core.parser.base_parser import ParsedFile, ParsedSymbol


class CodeGraphRepository:
    """
    Repository for code graph data access.

    All methods operate within the provided session without committing.
    The caller (service layer) is responsible for transaction management.
    """

    def __init__(self, db: AsyncSession, repository_id: str):
        """
        Initialize repository.

        Args:
            db: Async database session
            repository_id: UUID of the repository being indexed
        """
        self.db = db
        self.repository_id = repository_id

    # =========================================================================
    # File Operations
    # =========================================================================

    async def create_file(
        self,
        path: str,
        language: str,
        content_hash: str,
    ) -> IndexedFile:
        """Create or update an indexed file record."""
        # Check if file exists
        stmt = select(IndexedFile).where(
            and_(
                IndexedFile.repository_id == self.repository_id,
                IndexedFile.path == path,
            )
        )
        result = await self.db.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            existing.language = language
            existing.content_hash = content_hash
            return existing

        file = IndexedFile(
            repository_id=self.repository_id,
            path=path,
            language=language,
            content_hash=content_hash,
        )
        self.db.add(file)
        await self.db.flush()
        return file

    async def get_file_by_path(self, path: str) -> IndexedFile | None:
        """Get file by path."""
        stmt = select(IndexedFile).where(
            and_(
                IndexedFile.repository_id == self.repository_id,
                IndexedFile.path == path,
            )
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_file_hash(self, path: str) -> str | None:
        """Get content hash of a file for incremental indexing."""
        stmt = select(IndexedFile.content_hash).where(
            and_(
                IndexedFile.repository_id == self.repository_id,
                IndexedFile.path == path,
            )
        )
        result = await self.db.execute(stmt)
        row = result.scalar_one_or_none()
        return row if row else None

    async def delete_file(self, path: str) -> int:
        """
        Delete a file and all its related data.

        Returns number of deleted rows.
        """
        # Get file first
        file = await self.get_file_by_path(path)
        if not file:
            return 0

        # Delete file (cascade will handle symbols, imports)
        await self.db.delete(file)
        await self.db.flush()
        return 1

    async def get_all_files(self) -> list[dict]:
        """Get all files in the repository."""
        stmt = select(
            IndexedFile.path,
            IndexedFile.language,
            IndexedFile.content_hash,
        ).where(IndexedFile.repository_id == self.repository_id).order_by(
            IndexedFile.path
        )
        result = await self.db.execute(stmt)
        return [
            {'path': r.path, 'language': r.language, 'hash': r.content_hash}
            for r in result.all()
        ]

    # =========================================================================
    # Symbol Operations
    # =========================================================================

    async def create_symbol(
        self,
        file_id: str,
        symbol: ParsedSymbol,
    ) -> Symbol:
        """Create a symbol record."""
        sym = Symbol(
            file_id=file_id,
            name=symbol.name,
            symbol_type=symbol.type,
            line_start=symbol.line_start,
            line_end=symbol.line_end,
            signature=symbol.signature,
            docstring=symbol.docstring,
            parent_class_name=symbol.parent_class,
            content_hash=symbol.content_hash,
            decorators=symbol.decorators if symbol.decorators else None,
        )
        self.db.add(sym)
        await self.db.flush()
        return sym

    async def get_symbol_by_name(
        self,
        name: str,
        file_path: str | None = None,
    ) -> Symbol | None:
        """Get symbol by name, optionally filtered by file."""
        stmt = (
            select(Symbol)
            .join(IndexedFile)
            .where(
                and_(
                    IndexedFile.repository_id == self.repository_id,
                    Symbol.name == name,
                )
            )
        )
        if file_path:
            stmt = stmt.where(IndexedFile.path == file_path)

        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def find_symbol_by_name(self, name: str) -> Symbol | None:
        """Find first symbol matching name in repository."""
        stmt = (
            select(Symbol)
            .join(IndexedFile)
            .where(
                and_(
                    IndexedFile.repository_id == self.repository_id,
                    Symbol.name == name,
                )
            )
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_all_symbols(
        self,
        file_path: str | None = None,
    ) -> list[dict]:
        """Get all symbols, optionally filtered by file."""
        if file_path:
            stmt = (
                select(
                    Symbol.name,
                    Symbol.symbol_type,
                    Symbol.line_start,
                    Symbol.signature,
                    Symbol.docstring,
                )
                .join(IndexedFile)
                .where(
                    and_(
                        IndexedFile.repository_id == self.repository_id,
                        IndexedFile.path == file_path,
                    )
                )
                .order_by(Symbol.line_start)
            )
        else:
            stmt = (
                select(
                    IndexedFile.path,
                    Symbol.name,
                    Symbol.symbol_type,
                    Symbol.line_start,
                    Symbol.signature,
                )
                .join(IndexedFile)
                .where(IndexedFile.repository_id == self.repository_id)
                .order_by(IndexedFile.path, Symbol.line_start)
            )

        result = await self.db.execute(stmt)
        rows = result.all()

        if file_path:
            return [
                {
                    'name': r.name,
                    'type': r.symbol_type,
                    'line_start': r.line_start,
                    'signature': r.signature,
                    'docstring': r.docstring,
                }
                for r in rows
            ]
        else:
            return [
                {
                    'file': r.path,
                    'name': r.name,
                    'type': r.symbol_type,
                    'line_start': r.line_start,
                    'signature': r.signature,
                }
                for r in rows
            ]

    async def search_symbols(self, query: str, limit: int = 50) -> list[dict]:
        """Search symbols by name (case-insensitive contains)."""
        stmt = (
            select(
                IndexedFile.path,
                Symbol.name,
                Symbol.symbol_type,
                Symbol.line_start,
                Symbol.signature,
            )
            .join(IndexedFile)
            .where(
                and_(
                    IndexedFile.repository_id == self.repository_id,
                    func.lower(Symbol.name).contains(func.lower(query)),
                )
            )
            .order_by(Symbol.name)
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return [
            {
                'file': r.path,
                'name': r.name,
                'type': r.symbol_type,
                'line_start': r.line_start,
                'signature': r.signature,
            }
            for r in result.all()
        ]

    # =========================================================================
    # Call Relationship Operations
    # =========================================================================

    async def create_call(
        self,
        caller_id: str | None,
        callee_id: str | None,
        callee_name: str,
        line_number: int,
        is_external: bool = False,
    ) -> SymbolCall:
        """Create a call relationship."""
        call = SymbolCall(
            caller_id=caller_id,
            callee_id=callee_id,
            callee_name=callee_name,
            line_number=line_number,
            is_external=is_external,
            repository_id=self.repository_id,
        )
        self.db.add(call)
        await self.db.flush()
        return call

    async def get_callers(self, symbol_name: str) -> list[dict]:
        """Get all symbols that call a given symbol."""
        # Find the callee symbol first
        callee = await self.find_symbol_by_name(symbol_name)

        stmt = (
            select(
                Symbol.name,
                IndexedFile.path,
                SymbolCall.line_number,
                Symbol.symbol_type,
            )
            .join(Symbol, SymbolCall.caller_id == Symbol.id)
            .join(IndexedFile, Symbol.file_id == IndexedFile.id)
            .where(
                and_(
                    SymbolCall.repository_id == self.repository_id,
                    or_(
                        SymbolCall.callee_id == callee.id if callee else False,
                        SymbolCall.callee_name == symbol_name,
                    ),
                )
            )
            .order_by(IndexedFile.path, SymbolCall.line_number)
        )
        result = await self.db.execute(stmt)
        return [
            {
                'caller': r.name,
                'file': r.path,
                'line': r.line_number,
                'type': r.symbol_type,
            }
            for r in result.all()
        ]

    async def get_callees(self, symbol_name: str) -> list[dict]:
        """Get all symbols called by a given symbol."""
        # Find the caller symbol
        caller = await self.find_symbol_by_name(symbol_name)
        if not caller:
            return []

        stmt = (
            select(
                SymbolCall.callee_name,
                SymbolCall.line_number,
                SymbolCall.is_external,
                Symbol.symbol_type,
            )
            .outerjoin(Symbol, SymbolCall.callee_id == Symbol.id)
            .where(SymbolCall.caller_id == caller.id)
            .order_by(SymbolCall.line_number)
        )
        result = await self.db.execute(stmt)
        return [
            {
                'callee': r.callee_name,
                'line': r.line_number,
                'type': r.symbol_type if r.symbol_type else 'external',
            }
            for r in result.all()
        ]

    # =========================================================================
    # Import Operations
    # =========================================================================

    async def create_import(
        self,
        file_id: str,
        module: str,
        imported_name: str | None,
        alias: str | None,
        is_relative: bool,
        line_number: int,
    ) -> SymbolImport:
        """Create an import record."""
        imp = SymbolImport(
            file_id=file_id,
            module=module,
            imported_name=imported_name,
            alias=alias,
            is_relative=is_relative,
            line_number=line_number,
        )
        self.db.add(imp)
        await self.db.flush()
        return imp

    async def get_file_imports(self, file_path: str) -> list[dict]:
        """Get all imports for a file."""
        stmt = (
            select(
                SymbolImport.module,
                SymbolImport.imported_name,
            )
            .join(IndexedFile)
            .where(
                and_(
                    IndexedFile.repository_id == self.repository_id,
                    IndexedFile.path == file_path,
                )
            )
            .order_by(SymbolImport.module)
        )
        result = await self.db.execute(stmt)
        return [
            {'module': r.module, 'imported': r.imported_name or r.module}
            for r in result.all()
        ]

    # =========================================================================
    # Inheritance Operations
    # =========================================================================

    async def create_inheritance(
        self,
        child_class_id: str,
        parent_class_name: str,
        parent_class_id: str | None,
        line_number: int,
    ) -> SymbolInheritance:
        """Create an inheritance relationship."""
        inh = SymbolInheritance(
            child_class_id=child_class_id,
            parent_class_name=parent_class_name,
            parent_class_id=parent_class_id,
            line_number=line_number,
            repository_id=self.repository_id,
        )
        self.db.add(inh)
        await self.db.flush()
        return inh

    async def get_class_hierarchy(self, class_name: str) -> list[dict]:
        """
        Get inheritance chain for a class using recursive CTE.

        Returns list of class names in inheritance order.
        """
        # Find the starting class
        start_class = await self.find_symbol_by_name(class_name)
        if not start_class:
            return []

        # Use recursive query to find hierarchy
        hierarchy = [class_name]

        # Simple iterative approach for now (can optimize with CTE later)
        current_id = start_class.id
        visited = {current_id}
        max_depth = 20  # Prevent infinite loops

        for _ in range(max_depth):
            stmt = select(SymbolInheritance).where(
                and_(
                    SymbolInheritance.repository_id == self.repository_id,
                    SymbolInheritance.child_class_id == current_id,
                )
            )
            result = await self.db.execute(stmt)
            inheritance = result.scalar_one_or_none()

            if not inheritance:
                break

            hierarchy.append(inheritance.parent_class_name)

            if inheritance.parent_class_id:
                if inheritance.parent_class_id in visited:
                    break  # Circular inheritance detected
                visited.add(inheritance.parent_class_id)
                current_id = inheritance.parent_class_id
            else:
                break  # External parent class

        return [{'hierarchy': hierarchy}]

    # =========================================================================
    # Cleanup Operations
    # =========================================================================

    async def clear_repository(self) -> dict:
        """
        Delete all graph data for this repository.

        Returns count of deleted items.
        """
        # Delete in order due to foreign keys
        # Calls, Inheritances, Imports reference Symbols
        # Symbols reference Files

        # Delete calls
        calls_stmt = delete(SymbolCall).where(
            SymbolCall.repository_id == self.repository_id
        )
        calls_result = await self.db.execute(calls_stmt)

        # Delete inheritances
        inh_stmt = delete(SymbolInheritance).where(
            SymbolInheritance.repository_id == self.repository_id
        )
        inh_result = await self.db.execute(inh_stmt)

        # Delete files (cascade will handle symbols and imports)
        files_stmt = delete(IndexedFile).where(
            IndexedFile.repository_id == self.repository_id
        )
        files_result = await self.db.execute(files_stmt)

        await self.db.flush()

        return {
            'files_deleted': files_result.rowcount,
            'calls_deleted': calls_result.rowcount,
            'inheritances_deleted': inh_result.rowcount,
        }

    # =========================================================================
    # Statistics
    # =========================================================================

    async def get_stats(self) -> dict:
        """Get statistics about the repository graph."""
        # Count files
        files_stmt = select(func.count(IndexedFile.id)).where(
            IndexedFile.repository_id == self.repository_id
        )
        files_result = await self.db.execute(files_stmt)
        files_count = files_result.scalar() or 0

        # Count symbols by type
        symbols_stmt = (
            select(Symbol.symbol_type, func.count(Symbol.id))
            .join(IndexedFile)
            .where(IndexedFile.repository_id == self.repository_id)
            .group_by(Symbol.symbol_type)
        )
        symbols_result = await self.db.execute(symbols_stmt)
        symbol_counts = dict(symbols_result.all())

        return {
            'files': files_count,
            'symbols': sum(symbol_counts.values()),
            'classes': symbol_counts.get('class', 0),
            'functions': symbol_counts.get('function', 0),
            'methods': symbol_counts.get('method', 0),
        }
