"""
Index service for repository code indexing.

Handles:
- Full repository indexing
- Incremental indexing (only changed files)
- File traversal and parsing

This service uses PostgreSQL for code graph storage.
"""

import hashlib
from pathlib import Path
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from infra.config import settings
from models.repository import Repository, IndexStatus
from services.graph_service import GraphService
from core.parser.enhanced_python_parser import EnhancedPythonParser
from core.parser.base_parser import BaseParser
from core.logging_config import get_logger

logger = get_logger(__name__)

# Directories and files to skip during indexing
SKIP_DIRS = {
    '.git',
    '__pycache__',
    '.pytest_cache',
    '.mypy_cache',
    '.tox',
    '.venv',
    'venv',
    'env',
    'node_modules',
    '.idea',
    '.vscode',
    'dist',
    'build',
    '.eggs',
    '*.egg-info',
}

SKIP_FILES = {
    '.gitignore',
    '.gitattributes',
    '.dockerignore',
    'Dockerfile',
    'docker-compose.yml',
    '.env',
    '.env.example',
    'requirements.txt',
    'setup.py',
    'setup.cfg',
    'pyproject.toml',
    'poetry.lock',
    'Pipfile',
    'Pipfile.lock',
}


class IndexService:
    """Service for indexing repository code into PostgreSQL."""

    def __init__(self, db: AsyncSession, repository: Repository):
        """
        Initialize index service.

        Args:
            db: Database session
            repository: Repository model
        """
        self.db = db
        self.repo = repository
        # GraphService now requires db session
        self.graph = GraphService(db, str(repository.id))
        self.parsers: dict[str, BaseParser] = {}

        # Register available parsers
        self._register_parsers()

    def _register_parsers(self):
        """Register all available language parsers."""
        python_parser = EnhancedPythonParser()
        for ext in python_parser.file_extensions:
            self.parsers[ext] = python_parser

    def _get_parser(self, file_path: str) -> BaseParser | None:
        """Get parser for a file based on extension."""
        ext = Path(file_path).suffix
        return self.parsers.get(ext)

    def _should_skip_path(self, path: Path) -> bool:
        """Check if a path should be skipped during indexing."""
        # Skip hidden files/directories (except .py files)
        if path.name.startswith('.') and not path.name.endswith('.py'):
            return True

        # Skip specific directories
        if path.is_dir() and path.name in SKIP_DIRS:
            return True

        # Skip specific files
        if path.is_file() and path.name in SKIP_FILES:
            return True

        # Skip files without a parser
        if path.is_file() and not self._get_parser(str(path)):
            return True

        return False

    def _compute_file_hash(self, content: str) -> str:
        """Compute hash of file content."""
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def _get_local_path(self) -> Path:
        """Get local clone path for the repository."""
        return Path(settings.repo_storage_path) / str(self.repo.id)

    async def _update_status(
        self,
        status: IndexStatus,
        commit_sha: str | None = None,
    ):
        """Update repository index status."""
        self.repo.index_status = status.value

        if status == IndexStatus.INDEXED:
            self.repo.is_indexed = True
            self.repo.last_indexed_at = datetime.utcnow()
            if commit_sha:
                self.repo.last_indexed_commit = commit_sha

        elif status == IndexStatus.FAILED:
            self.repo.is_indexed = False

        # Flush changes but don't commit yet
        # The caller (route) will handle the final commit
        await self.db.flush()

    async def full_index(self, commit_sha: str | None = None) -> dict:
        """
        Perform full repository indexing.

        Clears existing graph data and re-indexes all files.

        Returns:
            Statistics about indexed content
        """
        local_path = self._get_local_path()
        if not local_path.exists():
            raise ValueError(f'Repository not cloned: {local_path}')

        logger.info(f'Starting full index of {self.repo.full_name}')
        await self._update_status(IndexStatus.INDEXING)

        stats = {
            'files_processed': 0,
            'files_skipped': 0,
            'symbols': 0,
            'imports': 0,
            'calls': 0,
            'inheritances': 0,
            'errors': [],
        }

        try:
            # Clear existing graph data
            await self.graph.clear_repository()

            # Store parsed files for second pass (creating relationships)
            parsed_files = []

            # Traverse and index all files
            for file_path in self._walk_directory(local_path):
                try:
                    parsed, file_stats = await self._index_file(file_path, local_path)
                    if parsed:
                        parsed_files.append(parsed)
                    stats['files_processed'] += 1
                    stats['symbols'] += file_stats.get('symbols', 0)
                    stats['imports'] += file_stats.get('imports', 0)
                except Exception as e:
                    logger.error(f'Failed to index {file_path}: {e}')
                    stats['errors'].append(str(file_path))

            # Create call and inheritance relationships after all symbols are indexed
            for parsed in parsed_files:
                try:
                    rel_stats = await self.graph.create_relationships_for_file(parsed)
                    stats['calls'] += rel_stats.get('calls', 0)
                    stats['inheritances'] += rel_stats.get('inheritances', 0)
                except Exception as e:
                    logger.error(f'Failed to create relationships for {parsed.file_path}: {e}')

            # Update status to indexed
            await self._update_status(IndexStatus.INDEXED, commit_sha)

            logger.info(
                f'Completed indexing {self.repo.full_name}: '
                f'{stats["files_processed"]} files, {stats["symbols"]} symbols'
            )

        except Exception as e:
            logger.error(f'Indexing failed for {self.repo.full_name}: {e}')
            await self._update_status(IndexStatus.FAILED)
            raise

        return stats

    async def incremental_index(self, changed_files: list[str]) -> dict:
        """
        Perform incremental indexing of changed files.

        Only re-indexes files that have changed since last index.

        Args:
            changed_files: List of file paths that changed

        Returns:
            Statistics about indexed content
        """
        local_path = self._get_local_path()
        if not local_path.exists():
            raise ValueError(f'Repository not cloned: {local_path}')

        logger.info(
            f'Starting incremental index of {self.repo.full_name}: '
            f'{len(changed_files)} files'
        )

        stats = {
            'files_processed': 0,
            'files_skipped': 0,
            'files_deleted': 0,
            'symbols': 0,
            'errors': [],
        }

        parsed_files = []

        for rel_path in changed_files:
            file_path = local_path / rel_path

            if not file_path.exists():
                # File was deleted - remove from graph
                await self.graph.delete_file(rel_path)
                stats['files_deleted'] += 1
                continue

            if self._should_skip_path(file_path):
                stats['files_skipped'] += 1
                continue

            try:
                # Check if file content changed
                content = file_path.read_text(encoding='utf-8', errors='ignore')
                new_hash = self._compute_file_hash(content)
                existing_hash = await self.graph.get_file_hash(rel_path)

                if existing_hash == new_hash:
                    stats['files_skipped'] += 1
                    continue

                # Re-index the file
                await self.graph.delete_file(rel_path)
                parsed, file_stats = await self._index_file(file_path, local_path)
                if parsed:
                    parsed_files.append(parsed)
                stats['files_processed'] += 1
                stats['symbols'] += file_stats.get('symbols', 0)

            except Exception as e:
                logger.error(f'Failed to index {file_path}: {e}')
                stats['errors'].append(str(rel_path))

        # Create relationships for changed files
        for parsed in parsed_files:
            try:
                await self.graph.create_relationships_for_file(parsed)
            except Exception as e:
                logger.error(f'Failed to create relationships for {parsed.file_path}: {e}')

        logger.info(
            f'Completed incremental index: '
            f'{stats["files_processed"]} updated, {stats["files_deleted"]} deleted'
        )

        return stats

    def _walk_directory(self, root: Path):
        """
        Walk directory and yield parseable files.

        Yields:
            Path objects for files that can be parsed
        """
        for path in root.rglob('*'):
            if self._should_skip_path(path):
                continue

            if path.is_file():
                yield path

    async def _index_file(self, file_path: Path, root: Path) -> tuple:
        """
        Index a single file.

        Args:
            file_path: Absolute path to file
            root: Repository root directory

        Returns:
            Tuple of (parsed_file, stats)
        """
        parser = self._get_parser(str(file_path))
        if not parser:
            return None, {}

        # Read file content
        content = file_path.read_text(encoding='utf-8', errors='ignore')

        # Get relative path for storage
        rel_path = str(file_path.relative_to(root))

        # Parse file
        parsed = parser.parse(rel_path, content)

        # Index into graph (creates file, symbols, imports)
        stats = await self.graph.index_file(parsed)

        return parsed, stats

    async def get_index_stats(self) -> dict:
        """Get statistics about the current index."""
        return await self.graph.get_graph_stats()
