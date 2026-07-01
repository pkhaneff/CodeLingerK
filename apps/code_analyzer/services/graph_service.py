"""
Graph service for code graph operations using PostgreSQL.

This service replaces the Neo4j-based implementation with PostgreSQL.
It handles:
- Creating/updating nodes (File, Symbol)
- Creating relationships (Calls, Imports, Inheritance)
- Querying the graph
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from apps.code_analyzer.repositories.code_graph_repository import CodeGraphRepository
from core.parser.base_parser import ParsedFile
from core.logging_config import get_logger

logger = get_logger(__name__)


class GraphService:
    """
    Service for managing code graph in PostgreSQL.

    This service orchestrates code graph operations and manages transactions.
    Data access is delegated to CodeGraphRepository.
    """

    def __init__(self, db: AsyncSession, repository_id: str):
        """
        Initialize graph service for a repository.

        Args:
            db: Async database session
            repository_id: UUID of the repository
        """
        self.db = db
        self.repo_id = repository_id
        self.repository = CodeGraphRepository(db, repository_id)

    # =========================================================================
    # Repository Operations
    # =========================================================================

    async def clear_repository(self) -> dict[str, Any]:
        """Delete all graph data for this repository."""
        result = await self.repository.clear_repository()
        logger.info(f'Cleared repository {self.repo_id} from graph')
        return result

    # =========================================================================
    # File Operations
    # =========================================================================

    async def create_file_node(
        self,
        file_path: str,
        language: str,
        content_hash: str,
    ) -> dict[str, Any]:
        """Create or update file node."""
        file = await self.repository.create_file(
            path=file_path,
            language=language,
            content_hash=content_hash,
        )
        return {'id': file.id, 'path': file.path}

    async def delete_file(self, file_path: str) -> dict[str, Any]:
        """Delete a file and all its symbols."""
        count = await self.repository.delete_file(file_path)
        return {'deleted': count}

    async def get_file_hash(self, file_path: str) -> str | None:
        """Get content hash of a file for incremental indexing."""
        return await self.repository.get_file_hash(file_path)

    async def get_files(self) -> list[dict[str, Any]]:
        """Get all files in the repository."""
        return await self.repository.get_all_files()

    # =========================================================================
    # Symbol Operations
    # =========================================================================

    async def create_symbol_node(
        self,
        file_id: str,
        symbol,
    ) -> dict[str, Any]:
        """Create or update a symbol node."""
        sym = await self.repository.create_symbol(file_id, symbol)
        return {'id': sym.id, 'name': sym.name, 'type': sym.symbol_type}

    async def get_symbols(
        self, file_path: str | None = None
    ) -> list[dict[str, Any]]:
        """Get all symbols, optionally filtered by file."""
        return await self.repository.get_all_symbols(file_path)

    async def search_symbols(self, query: str) -> list[dict[str, Any]]:
        """Search symbols by name."""
        return await self.repository.search_symbols(query)

    # =========================================================================
    # Relationship Operations
    # =========================================================================

    async def create_import_relationship(
        self,
        file_id: str,
        module: str,
        imported_name: str | None,
        alias: str | None = None,
        is_relative: bool = False,
        line_number: int = 0,
    ) -> dict[str, Any]:
        """Create import relationship."""
        imp = await self.repository.create_import(
            file_id=file_id,
            module=module,
            imported_name=imported_name,
            alias=alias,
            is_relative=is_relative,
            line_number=line_number,
        )
        return {'id': imp.id, 'module': imp.module}

    async def create_call_relationship(
        self,
        caller_name: str,
        callee_name: str,
        file_path: str,
        line_number: int,
    ) -> dict[str, Any]:
        """Create call relationship between symbols."""
        # Find caller symbol
        caller = await self.repository.find_symbol_by_name(caller_name)
        if not caller:
            return {'created': False, 'reason': 'caller_not_found'}

        # Find callee symbol (may not exist if external)
        callee = await self.repository.find_symbol_by_name(callee_name)

        call = await self.repository.create_call(
            caller_id=caller.id,
            callee_id=callee.id if callee else None,
            callee_name=callee_name,
            line_number=line_number,
            is_external=callee is None,
        )
        return {'id': call.id, 'is_external': call.is_external}

    async def create_extends_relationship(
        self,
        child_class_name: str,
        parent_class_name: str,
        file_path: str,
        line_number: int = 0,
    ) -> dict[str, Any]:
        """Create extends relationship for class inheritance."""
        # Find child class
        child = await self.repository.get_symbol_by_name(
            child_class_name, file_path
        )
        if not child:
            return {'created': False, 'reason': 'child_not_found'}

        # Find parent class (may not exist if external)
        parent = await self.repository.find_symbol_by_name(parent_class_name)

        inh = await self.repository.create_inheritance(
            child_class_id=child.id,
            parent_class_name=parent_class_name,
            parent_class_id=parent.id if parent else None,
            line_number=line_number,
        )
        return {'id': inh.id}

    # =========================================================================
    # Query Operations
    # =========================================================================

    async def get_callers(self, symbol_name: str) -> list[dict[str, Any]]:
        """Get all symbols that call a given symbol."""
        return await self.repository.get_callers(symbol_name)

    async def get_callees(self, symbol_name: str) -> list[dict[str, Any]]:
        """Get all symbols called by a given symbol."""
        return await self.repository.get_callees(symbol_name)

    async def get_dependencies(self, file_path: str) -> list[dict[str, Any]]:
        """Get all imports/dependencies of a file."""
        return await self.repository.get_file_imports(file_path)

    async def get_class_hierarchy(self, class_name: str) -> list[dict[str, Any]]:
        """Get inheritance chain for a class."""
        return await self.repository.get_class_hierarchy(class_name)

    async def get_graph_stats(self) -> dict[str, int]:
        """Get statistics about the repository graph."""
        return await self.repository.get_stats()

    # =========================================================================
    # Index a complete parsed file
    # =========================================================================

    async def index_file(self, parsed: ParsedFile) -> dict[str, Any]:
        """
        Index a complete parsed file into the graph.

        Creates:
        - File node
        - Symbol nodes (Class, Function, Method)
        - Import relationships

        Note: Call and inheritance relationships are created in a second pass
        via create_relationships_for_file() after all symbols are indexed.
        """
        stats = {
            'files': 0,
            'symbols': 0,
            'imports': 0,
        }

        # Create file node
        file = await self.repository.create_file(
            path=parsed.file_path,
            language=parsed.language,
            content_hash=parsed.content_hash,
        )
        stats['files'] = 1

        # Create symbol nodes
        for symbol in parsed.symbols:
            await self.repository.create_symbol(file.id, symbol)
            stats['symbols'] += 1

        # Create import records
        for imp in parsed.imports:
            await self.repository.create_import(
                file_id=file.id,
                module=imp.module,
                imported_name=imp.name,
                alias=imp.alias,
                is_relative=imp.is_relative,
                line_number=imp.line_number,
            )
            stats['imports'] += 1

        logger.debug(f'Indexed {parsed.file_path}: {stats}')
        return stats

    async def create_relationships_for_file(
        self,
        parsed: ParsedFile,
    ) -> dict[str, Any]:
        """
        Create call and inheritance relationships for a parsed file.

        This is done in a second pass after all symbols are indexed,
        because callees/parents may be defined in other files.
        """
        stats = {
            'calls': 0,
            'inheritances': 0,
        }

        # Create call relationships
        for call in parsed.calls:
            callee_name = call.callee
            if '.' in callee_name:
                callee_name = callee_name.split('.')[-1]

            result = await self.create_call_relationship(
                caller_name=call.caller,
                callee_name=callee_name,
                file_path=parsed.file_path,
                line_number=call.line_number,
            )
            if result.get('id'):
                stats['calls'] += 1

        # Create inheritance relationships
        for inh in parsed.inheritances:
            result = await self.create_extends_relationship(
                child_class_name=inh.child_class,
                parent_class_name=inh.parent_class,
                file_path=parsed.file_path,
                line_number=inh.line_number,
            )
            if result.get('id'):
                stats['inheritances'] += 1

        return stats
