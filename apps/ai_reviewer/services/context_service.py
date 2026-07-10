"""
ContextService - Build context from PR diff for AI review.

This service parses the diff content from a snapshot and builds
structured context that can be used by the AI review service.

Process:
1. Parse diff hunks
2. Extract changed files and line ranges
3. Build context with file content
4. Count tokens for budget management
5. Truncate if needed to fit token limit
"""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pathlib import Path
from infra.config import settings

from core.diff_parser import DiffParser, ParsedDiff
from core.logger import get_logger
from infra.redis_client import redis_client
from apps.ai_reviewer.tokenizer import Tokenizer
from apps.ai_reviewer.models.snapshot import Snapshot, SnapshotStatus

logger = get_logger(__name__)

# Default token limit for context
DEFAULT_MAX_CONTEXT_TOKENS = 50000

# Approximate tokens per character (for estimation)
TOKENS_PER_CHAR = 0.25


@dataclass
class FileContext:
    """Context information for a single file."""

    file_path: str
    status: str  # 'added', 'modified', 'deleted', 'renamed'
    hunks: list[dict[str, Any]] = field(default_factory=list)
    additions: int = 0
    deletions: int = 0
    old_path: str | None = None  # For renamed files

    @property
    def total_changes(self) -> int:
        """Total number of changed lines."""
        return self.additions + self.deletions


@dataclass
class SnapshotContext:
    """
    Full context for a snapshot.

    Contains all parsed diff information needed for AI review.
    """

    snapshot_id: str
    commit_sha: str
    files: list[FileContext] = field(default_factory=list)
    total_additions: int = 0
    total_deletions: int = 0
    estimated_tokens: int = 0
    level2_functions: dict[str, list[dict]] = field(default_factory=dict)
    level3_dependencies: list[str] = field(default_factory=list)
    level4_semantic_search: list[str] = field(default_factory=list)

    @property
    def file_count(self) -> int:
        """Number of files in context."""
        return len(self.files)

    @property
    def total_changes(self) -> int:
        """Total number of changed lines."""
        return self.total_additions + self.total_deletions

    def to_dict(self) -> dict[str, Any]:
        """Convert context to dictionary for storage."""
        return {
            'snapshot_id': self.snapshot_id,
            'commit_sha': self.commit_sha,
            'file_count': self.file_count,
            'total_additions': self.total_additions,
            'total_deletions': self.total_deletions,
            'estimated_tokens': self.estimated_tokens,
            'files': [
                {
                    'file_path': f.file_path,
                    'status': f.status,
                    'additions': f.additions,
                    'deletions': f.deletions,
                    'old_path': f.old_path,
                    'hunk_count': len(f.hunks),
                }
                for f in self.files
            ],
        }


class ContextService:
    """
    Build context from PR diff for AI review.

    Responsibilities:
    - Parse diff content into structured format
    - Extract changed files and line ranges
    - Build context with full diff information
    - Estimate token count for budget management
    """

    def __init__(
        self,
        db: AsyncSession,
        max_tokens: int | None = None,
        tokenizer: Tokenizer | None = None,
    ):
        """
        Initialize context service.

        Args:
            db: Database session
            max_tokens: Maximum tokens allowed in context
            tokenizer: Tokenizer strategy instance
        """
        self.db = db
        self.max_tokens = max_tokens or getattr(settings, 'ai_soft_budget', DEFAULT_MAX_CONTEXT_TOKENS)
        
        if tokenizer is None:
            from apps.ai_reviewer.tokenizer import TokenizerFactory
            self.tokenizer = TokenizerFactory.get_tokenizer(
                getattr(settings, 'ai_provider', 'openai'),
                getattr(settings, 'ai_model', 'gpt-4')
            )
        else:
            self.tokenizer = tokenizer
            
        from apps.ai_reviewer.services.context_formatter import ReviewContextFormatter
        self.formatter = ReviewContextFormatter(self.tokenizer)

    async def build_context(self, snapshot: Snapshot) -> SnapshotContext:
        """
        Build context from snapshot diff.

        Args:
            snapshot: Snapshot with diff_content

        Returns:
            SnapshotContext with parsed diff information
        """
        logger.info(f'Building context for snapshot {snapshot.id[:8]}')

        # Check if pull_request is loaded to avoid lazy loading MissingGreenlet error
        from sqlalchemy import inspect
        insp = inspect(snapshot)
        if 'pull_request' in insp.unloaded:
            from apps.ai_reviewer.models.pull_request import PullRequest
            stmt = select(PullRequest).where(PullRequest.id == snapshot.pull_request_id)
            pr = (await self.db.execute(stmt)).scalar_one_or_none()
            snapshot.pull_request = pr

        # 1. Level 1: Parse the diff content
        files = self._parse_diff(snapshot.diff_content or '')

        # Calculate totals
        total_additions = sum(f.additions for f in files)
        total_deletions = sum(f.deletions for f in files)

        # Build context object
        context = SnapshotContext(
            snapshot_id=str(snapshot.id),
            commit_sha=snapshot.commit_sha,
            files=files,
            total_additions=total_additions,
            total_deletions=total_deletions,
        )

        repository_id = snapshot.pull_request.repository_id

        # 2. Level 2 & 3: Function bodies & Call Graph dependencies
        from core.git_manager import GitManager
        from apps.code_analyzer.models.code_graph import Symbol, IndexedFile
        from apps.code_analyzer.services.graph_service import GraphService

        repo_path = Path(settings.repo_storage_path) / str(repository_id)
        git_mgr = None
        if repo_path.exists():
            try:
                git_mgr = GitManager(str(repo_path))
            except Exception as e:
                logger.warning(f'Could not init GitManager for embedding: {e}')

        graph_svc = GraphService(self.db, str(repository_id))

        # Find functions overlapping with changed diff hunks
        for f in files:
            file_path = f.file_path

            # Query symbols for this file path
            stmt = select(Symbol).join(IndexedFile, Symbol.file_id == IndexedFile.id).where(
                IndexedFile.repository_id == repository_id,
                IndexedFile.path == file_path
            )
            file_symbols = (await self.db.execute(stmt)).scalars().all()
            if not file_symbols:
                continue

            raw_code = ''
            if git_mgr:
                try:
                    raw_code = git_mgr.get_file_content(file_path, snapshot.commit_sha) or ''
                except Exception as e:
                    logger.warning(f'Could not read git file content for {file_path}: {e}')

            # Check overlaps for each hunk
            overlapping_symbols = []
            for hunk in f.hunks:
                hunk_start = hunk.get('new_start', 1)
                hunk_end = hunk_start + hunk.get('new_count', 0)

                for sym in file_symbols:
                    sym_end = sym.line_end if sym.line_end is not None else sym.line_start + 10
                    # Overlap check
                    if sym.line_start <= hunk_end and sym_end >= hunk_start:
                        if sym not in overlapping_symbols:
                            overlapping_symbols.append(sym)

            if overlapping_symbols and raw_code:
                context.level2_functions[file_path] = []
                lines = raw_code.splitlines()

                for sym in overlapping_symbols:
                    start = max(0, sym.line_start - 1)
                    end = sym.line_end if sym.line_end is not None else len(lines)
                    sym_code = '\n'.join(lines[start:end])

                    context.level2_functions[file_path].append({
                        'name': sym.name,
                        'symbol_type': sym.symbol_type,
                        'code': sym_code,
                        'lines': f'{sym.line_start}-{sym.line_end or "end"}',
                    })

                    # Level 3: Fetch callers and callees for this symbol
                    try:
                        callers = await graph_svc.get_callers(sym.name)
                        for c in callers:
                            context.level3_dependencies.append(
                                f"Symbol `{c['caller']}` ({c['symbol_type']}) in file `{c['file_path']}` calls `{sym.name}` at line {c['line_number']}"
                            )

                        callees = await graph_svc.get_callees(sym.name)
                        for c in callees:
                            context.level3_dependencies.append(
                                f"Symbol `{sym.name}` calls `{c['callee']}` at line {c['line']}"
                            )
                    except Exception as e:
                        logger.warning(f'Could not load callers/callees for symbol {sym.name}: {e}')

        # 3. Level 4: PGVector Semantic Search
        # Embed the PR title/description to find similar references in the codebase
        from apps.code_analyzer.services.embedding_service import EmbeddingClient
        from apps.code_analyzer.models.code_graph import FileChunk
        embed_client = EmbeddingClient()

        pr_title = snapshot.pull_request.title
        pr_desc = snapshot.pull_request.html_url or ''

        query_text = (
            f'Pull Request: {pr_title or ""}\n'
            f'Description: {pr_desc}\n'
            f'Files: ' + ', '.join([f.file_path for f in files])
        )

        try:
            query_vector = await embed_client.get_embedding(query_text)
            if query_vector:
                # Query symbols
                stmt_sym = select(Symbol, IndexedFile.path).join(IndexedFile, Symbol.file_id == IndexedFile.id).where(
                    IndexedFile.repository_id == repository_id,
                    Symbol.embedding.isnot(None),
                    IndexedFile.path.notin_([f.file_path for f in files])  # skip files already in diff
                ).order_by(Symbol.embedding.cosine_distance(query_vector)).limit(3)

                sym_res = (await self.db.execute(stmt_sym)).all()
                for sym, path in sym_res:
                    context.level4_semantic_search.append(
                        f"Semantically relevant symbol found: `{sym.name}` ({sym.symbol_type}) in file `{path}` (lines {sym.line_start}-{sym.line_end or 'end'})"
                    )

                # Query chunks
                stmt_chk = select(FileChunk, IndexedFile.path).join(IndexedFile, FileChunk.file_id == IndexedFile.id).where(
                    IndexedFile.repository_id == repository_id,
                    FileChunk.embedding.isnot(None),
                    IndexedFile.path.notin_([f.file_path for f in files])
                ).order_by(FileChunk.embedding.cosine_distance(query_vector)).limit(2)

                chk_res = (await self.db.execute(stmt_chk)).all()
                for chk, path in chk_res:
                    snippet = chk.content.splitlines()[:5]
                    snippet_str = '\n'.join(snippet)
                    context.level4_semantic_search.append(
                        f"Semantically relevant code chunk in file `{path}` (chunk index {chk.chunk_index}):\n```\n{snippet_str}\n```"
                    )
        except Exception as e:
            logger.warning(f'Could not load pgvector Level 4 semantic search context: {e}')

        # 4. Prune context to fit budget
        self._prune_context(context)

        # Estimate final token count of the pruned context
        final_context_str = self.build_review_prompt_context(context, include_full_diff=True)
        estimated_tokens = self._count_tokens(final_context_str)
        context.estimated_tokens = estimated_tokens

        # Update snapshot with token count
        snapshot.context_token_count = estimated_tokens
        await self.db.flush()

        logger.info(
            f'Context built: {context.file_count} files, '
            f'+{context.total_additions}/-{context.total_deletions}, '
            f'~{estimated_tokens} tokens'
        )

        return context

    def _prune_context(self, context: SnapshotContext) -> None:
        """
        Prune SnapshotContext using the Priority Pruner strategy
        to fit within self.max_tokens budget.
        """
        def get_estimated_size(ctx: SnapshotContext) -> int:
            text = self.build_review_prompt_context(ctx, include_full_diff=True)
            return self._count_tokens(text)

        total_tokens = get_estimated_size(context)
        if total_tokens <= self.max_tokens:
            return

        logger.info(f'Context size {total_tokens} exceeds budget {self.max_tokens}. Running PriorityPruner.')

        # Step 1: Drop Level 4 context (pgvector results)
        if context.level4_semantic_search:
            logger.info('Pruner Step 1: Dropping Level 4 semantic search references.')
            context.level4_semantic_search = []
            total_tokens = get_estimated_size(context)
            if total_tokens <= self.max_tokens:
                return

        # Step 2: Drop Level 3 context (dependencies)
        if context.level3_dependencies:
            logger.info('Pruner Step 2: Dropping Level 3 callers/callees dependencies.')
            context.level3_dependencies = []
            total_tokens = get_estimated_size(context)
            if total_tokens <= self.max_tokens:
                return

        # Step 3: Strip comments and docstrings from Level 2 function bodies
        if context.level2_functions:
            logger.info('Pruner Step 3: Stripping comments and docstrings from Level 2 functions.')
            import re
            for file_path, functions in context.level2_functions.items():
                for fn in functions:
                    # Strip python single-line comments
                    code = re.sub(r'#.*$', '', fn['code'], flags=re.MULTILINE)
                    # Strip python multi-line docstrings (double quotes)
                    code = re.sub(r'""".*?"""', '', code, flags=re.DOTALL)
                    # Strip python multi-line docstrings (single quotes)
                    code = re.sub(r"'''.*?'''", '', code, flags=re.DOTALL)
                    fn['code'] = code
            total_tokens = get_estimated_size(context)
            if total_tokens <= self.max_tokens:
                return

        # Step 4: Drop unit test files from context
        test_files = [f for f in context.files if 'test' in f.file_path.lower() or 'spec' in f.file_path.lower()]
        if test_files:
            logger.info('Pruner Step 4: Dropping test files from context.')
            context.files = [f for f in context.files if f not in test_files]
            for f in test_files:
                context.level2_functions.pop(f.file_path, None)
            total_tokens = get_estimated_size(context)
            if total_tokens <= self.max_tokens:
                return

        # Step 5: Drop Level 2 function bodies entirely
        if context.level2_functions:
            logger.info('Pruner Step 5: Dropping Level 2 function bodies completely.')
            context.level2_functions = {}
            total_tokens = get_estimated_size(context)
            if total_tokens <= self.max_tokens:
                return

        # Step 6: Truncate diff content (Level 1 hunks) thô
        logger.info('Pruner Step 6: Truncating diff hunks count.')
        for f in context.files:
            if len(f.hunks) > 5:
                f.hunks = f.hunks[:5]

    def _count_tokens(self, text: str) -> int:
        """Deprecated - count tokens using the unified tokenizer strategy."""
        return self.tokenizer.count_tokens(text)

    def _parse_diff(self, diff_content: str) -> list[FileContext]:
        """
        Parse diff content into FileContext objects.

        Args:
            diff_content: Raw diff string

        Returns:
            List of FileContext objects
        """
        if not diff_content:
            return []

        parsed_diffs: list[ParsedDiff] = DiffParser.parse_multi_file_diff(diff_content)

        files = []
        for parsed in parsed_diffs:
            # Determine file status
            if parsed.is_new_file:
                status = 'added'
            elif parsed.is_deleted_file:
                status = 'deleted'
            elif parsed.is_renamed:
                status = 'renamed'
            else:
                status = 'modified'

            # Build hunks list
            hunks = []
            additions = 0
            deletions = 0

            for hunk in parsed.hunks:
                added_lines = hunk.get_added_lines()
                deleted_lines = hunk.get_deleted_lines()

                hunks.append({
                    'old_start': hunk.old_start,
                    'old_count': hunk.old_count,
                    'new_start': hunk.new_start,
                    'new_count': hunk.new_count,
                    'added_lines': [
                        {'line': ln, 'content': content}
                        for ln, content in added_lines
                    ],
                    'deleted_lines': [
                        {'line': ln, 'content': content}
                        for ln, content in deleted_lines
                    ],
                })

                additions += len(added_lines)
                deletions += len(deleted_lines)

            file_context = FileContext(
                file_path=parsed.file_path,
                status=status,
                hunks=hunks,
                additions=additions,
                deletions=deletions,
                old_path=parsed.old_path if parsed.is_renamed else None,
            )

            files.append(file_context)

        return files

    def _estimate_tokens(self, content: str) -> int:
        """
        Estimate token count for content.

        Uses a simple character-based estimation.
        For more accurate counting, integrate with tiktoken or similar.

        Args:
            content: Text content

        Returns:
            Estimated token count
        """
        return int(len(content) * TOKENS_PER_CHAR)

    def build_review_prompt_context(
        self,
        context: SnapshotContext,
        include_full_diff: bool = True,
    ) -> str:
        """
        Deprecated - build context using the unified context formatter.
        """
        return self.formatter.format_context(
            context=context,
            limit_hunk_lines=None if include_full_diff else 20
        )

    async def get_snapshot(self, snapshot_id: str) -> Snapshot | None:
        """Get snapshot by ID."""
        result = await self.db.execute(
            select(Snapshot).where(Snapshot.id == snapshot_id)
        )
        return result.scalar_one_or_none()

    async def update_snapshot_status(
        self,
        snapshot: Snapshot,
        status: SnapshotStatus,
    ) -> None:
        """Update snapshot status."""
        snapshot.status = status.value
        await self.db.flush()
        await redis_client.publish_snapshot_status(snapshot.id, snapshot.status)
