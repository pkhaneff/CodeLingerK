import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.ext.asyncio import AsyncSession

from apps.auth.models.user import User
from apps.repositories.models.repository import Repository
from apps.ai_reviewer.models.pull_request import PullRequest
from apps.ai_reviewer.models.snapshot import Snapshot
from apps.ai_reviewer.models.review import Review, CommentSeverity
from apps.ai_reviewer.services.context_service import SnapshotContext, FileContext
from apps.ai_reviewer.services.ai_review_service import ChunkPlanner, Synthesizer, AIReviewService, ReviewPass


def test_chunk_planner_splits():
    # Set up context with 3 large files
    context = SnapshotContext(
        snapshot_id='fake-snap-id',
        commit_sha='abcdef123456',
        files=[
            FileContext(
                file_path='src/a.py',
                status='modified',
                additions=1000,
                deletions=0,
                hunks=[{'old_start': 1, 'old_count': 0, 'new_start': 1, 'new_count': 1000, 'added_lines': [{'content': 'line'}] * 1000, 'deleted_lines': []}]
            ),
            FileContext(
                file_path='src/b.py',
                status='modified',
                additions=1000,
                deletions=0,
                hunks=[{'old_start': 1, 'old_count': 0, 'new_start': 1, 'new_count': 1000, 'added_lines': [{'content': 'line'}] * 1000, 'deleted_lines': []}]
            ),
            FileContext(
                file_path='src/c.py',
                status='modified',
                additions=1000,
                deletions=0,
                hunks=[{'old_start': 1, 'old_count': 0, 'new_start': 1, 'new_count': 1000, 'added_lines': [{'content': 'line'}] * 1000, 'deleted_lines': []}]
            )
        ],
        total_additions=3000,
        total_deletions=0,
        estimated_tokens=3000,
    )

    # Set soft budget to ~200 characters (extremely small) so it is forced to split file-by-file
    planner = ChunkPlanner(soft_budget=50)
    chunks = planner.plan_chunks(context)
    
    assert len(chunks) == 3
    assert chunks[0].files[0].file_path == 'src/a.py'
    assert chunks[1].files[0].file_path == 'src/b.py'
    assert chunks[2].files[0].file_path == 'src/c.py'


def test_synthesizer_deduplicates():
    class DummyComment:
        def __init__(self, file_path, line_start, explanation):
            self.file_path = file_path
            self.line_start = line_start
            self.explanation = explanation

    comments = [
        DummyComment('src/a.py', 10, 'Use list comprehension instead.'),
        DummyComment('src/a.py', 10, 'Use list comprehension instead.  '), # whitespace variation
        DummyComment('src/b.py', 20, 'Add type hint.'),
    ]

    syn = Synthesizer()
    merged = syn.merge_comments(comments)
    
    assert len(merged) == 2
    assert merged[0].file_path == 'src/a.py'
    assert merged[1].file_path == 'src/b.py'
