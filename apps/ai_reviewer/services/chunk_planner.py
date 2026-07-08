from typing import Any
from apps.ai_reviewer.services.context_service import SnapshotContext, FileContext
from apps.ai_reviewer.models.layer import Layer
from core.logger import get_logger

logger = get_logger(__name__)


class ChunkSplitRequiredException(Exception):
    """Raised when a pass gets truncated and we can split chunks further."""
    pass


class ChunkPlanner:
    """Plans how to split a large review context into smaller review chunks."""

    def __init__(self, formatter_or_budget: Any = None, soft_budget: int = 50000):
        from apps.ai_reviewer.services.context_formatter import ReviewContextFormatter
        
        if isinstance(formatter_or_budget, int):
            self.soft_budget = formatter_or_budget
            from apps.ai_reviewer.tokenizer import TokenizerFactory
            tokenizer = TokenizerFactory.get_tokenizer("openai", "cl100k_base")
            self.formatter = ReviewContextFormatter(tokenizer)
        else:
            self.formatter = formatter_or_budget
            self.soft_budget = soft_budget
            
        if self.formatter is None:
            from apps.ai_reviewer.tokenizer import TokenizerFactory
            tokenizer = TokenizerFactory.get_tokenizer("openai", "cl100k_base")
            self.formatter = ReviewContextFormatter(tokenizer)

    def plan_chunks(
        self,
        context: SnapshotContext,
        layers: list[Layer] | None = None,
        external: Any = None,
        rules_section: str = '',
        static_section: str = '',
    ) -> list[SnapshotContext]:
        """
        Split a SnapshotContext into multiple SnapshotContext chunks
        such that each chunk's estimated tokens fits within the soft budget.
        """
        total_initial_tokens = self.formatter.count_tokens(
            context,
            layers=layers,
            external=external,
            rules_section=rules_section,
            static_section=static_section,
        )
        if total_initial_tokens <= self.soft_budget:
            context.estimated_tokens = total_initial_tokens
            return [context]

        logger.info(f'PR size ({total_initial_tokens} tokens) exceeds budget ({self.soft_budget}). Splitting into chunks.')

        chunks: list[SnapshotContext] = []
        current_files: list[FileContext] = []
        current_additions = 0
        current_deletions = 0

        for f in context.files:
            test_ctx = SnapshotContext(
                snapshot_id=context.snapshot_id,
                commit_sha=context.commit_sha,
                files=current_files + [f],
                total_additions=current_additions + f.additions,
                total_deletions=current_deletions + f.deletions,
            )
            # Map Level 2 functions for testing size
            test_ctx.level2_functions = {}
            if getattr(context, 'level2_functions', None):
                for fp in [x.file_path for x in test_ctx.files]:
                    if fp in context.level2_functions:
                        test_ctx.level2_functions[fp] = context.level2_functions[fp]

            test_tokens = self.formatter.count_tokens(
                test_ctx,
                layers=layers,
                external=external,
                rules_section=rules_section,
                static_section=static_section,
            )

            if current_files and test_tokens > self.soft_budget:
                chunk = SnapshotContext(
                    snapshot_id=context.snapshot_id,
                    commit_sha=context.commit_sha,
                    files=current_files,
                    total_additions=current_additions,
                    total_deletions=current_deletions,
                )
                chunks.append(chunk)
                current_files = [f]
                current_additions = f.additions
                current_deletions = f.deletions
            else:
                current_files.append(f)
                current_additions += f.additions
                current_deletions += f.deletions

        if current_files:
            chunk = SnapshotContext(
                snapshot_id=context.snapshot_id,
                commit_sha=context.commit_sha,
                files=current_files,
                total_additions=current_additions,
                total_deletions=current_deletions,
            )
            chunks.append(chunk)

        # Populate other metadata fields for all chunks
        for chunk in chunks:
            chunk.level3_dependencies = context.level3_dependencies
            chunk.level4_semantic_search = context.level4_semantic_search
            if getattr(context, 'level2_functions', None):
                chunk.level2_functions = {
                    fp: context.level2_functions[fp]
                    for fp in [x.file_path for x in chunk.files]
                    if fp in context.level2_functions
                }
            chunk.estimated_tokens = self.formatter.count_tokens(
                chunk,
                layers=layers,
                external=external,
                rules_section=rules_section,
                static_section=static_section,
            )

        logger.info(f'Planned {len(chunks)} chunks for review.')
        return chunks
