import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.ext.asyncio import AsyncSession

from apps.auth.models.user import User
from apps.repositories.models.repository import Repository
from apps.ai_reviewer.models.pull_request import PullRequest
from apps.ai_reviewer.models.snapshot import Snapshot
from apps.ai_reviewer.models.review import Review
from apps.ai_reviewer.services.context_service import ContextService, SnapshotContext, FileContext


@pytest.mark.asyncio
async def test_pruner_order():
    # Setup context with large diff
    context = SnapshotContext(
        snapshot_id='fake-snap-id',
        commit_sha='abcdef123456',
        files=[
            FileContext(
                file_path='tests/test_foo.py',
                status='modified',
                additions=10,
                deletions=5,
                hunks=[{'old_start': 1, 'old_count': 10, 'new_start': 1, 'new_count': 10, 'added_lines': [], 'deleted_lines': []}]
            ),
            FileContext(
                file_path='src/foo.py',
                status='modified',
                additions=20,
                deletions=10,
                hunks=[{'old_start': 1, 'old_count': 20, 'new_start': 1, 'new_count': 20, 'added_lines': [], 'deleted_lines': []}]
            )
        ],
        total_additions=30,
        total_deletions=15,
    )

    # Attach levels with long contents
    context.level4_semantic_search = ['level 4 ref long content ' * 100]  # ~500 tokens
    context.level3_dependencies = ['level 3 dep long content ' * 100]      # ~500 tokens
    context.level2_functions = {
        'src/foo.py': [
            {
                'name': 'foo_func',
                'symbol_type': 'function',
                'code': '# Comment\n' + 'def foo_func():\n' + '    """Docstring"""\n' + '    pass\n' + '    # comment line\n' * 50,
                'lines': '1-100'
            }
        ]
    }

    mock_db = AsyncMock(spec=AsyncSession)
    
    # 1. Initialize ContextService with high budget (should not prune)
    service = ContextService(mock_db, max_tokens=10000)
    service._prune_context(context)
    
    assert len(context.level4_semantic_search) == 1
    assert len(context.level3_dependencies) == 1
    assert len(context.level2_functions) == 1

    # 2. Set lower budget: should prune level 4 first
    # Total tokens is around 2076. Let's set budget to 1600.
    service = ContextService(mock_db, max_tokens=1600)
    service._prune_context(context)
    assert len(context.level4_semantic_search) == 0
    assert len(context.level3_dependencies) == 1

    # 3. Set budget even lower: should prune level 3
    # Remaining tokens is around 1576. Set budget to 1100.
    service = ContextService(mock_db, max_tokens=1100)
    service._prune_context(context)
    assert len(context.level3_dependencies) == 0
    assert len(context.level2_functions) == 1
    # Check that comments/docstrings are still there because 1100 budget was met
    code = context.level2_functions['src/foo.py'][0]['code']
    assert '# Comment' in code
    assert '"""Docstring"""' in code

    # 4. Set budget to 350: should strip comments and docstrings
    service = ContextService(mock_db, max_tokens=350)
    service._prune_context(context)
    code = context.level2_functions['src/foo.py'][0]['code']
    assert '# Comment' not in code
    assert '"""Docstring"""' not in code
    # But files still unchanged
    assert len(context.files) == 2

    # 5. Set budget to 200: should drop test files
    service = ContextService(mock_db, max_tokens=200)
    service._prune_context(context)
    file_paths = [f.file_path for f in context.files]
    assert 'tests/test_foo.py' not in file_paths
    assert len(context.files) == 1

    # 6. Set budget to 100: should drop level 2 functions completely
    service = ContextService(mock_db, max_tokens=100)
    service._prune_context(context)
    assert len(context.level2_functions) == 0
