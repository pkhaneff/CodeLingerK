import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.ext.asyncio import AsyncSession

from apps.auth.models.user import User
from apps.repositories.models.repository import Repository
from apps.ai_reviewer.models.pull_request import PullRequest
from apps.ai_reviewer.models.snapshot import Snapshot
from apps.ai_reviewer.models.review import Review
from apps.code_analyzer.services.embedding_service import EmbeddingClient, EmbeddingService
from apps.code_analyzer.models.code_graph import Symbol, FileChunk, IndexedFile


class _FakeEmbeddingsResponse:
    def __init__(self, embeddings):
        self.data = [
            MagicMock(index=idx, embedding=emb)
            for idx, emb in enumerate(embeddings)
        ]


@pytest.mark.asyncio
async def test_embedding_client_batching():
    # Mock AsyncOpenAI
    mock_openai = MagicMock()
    mock_openai.embeddings = MagicMock()
    mock_openai.embeddings.create = AsyncMock()

    # Configure mock responses
    fake_vector = [0.1] * 1536
    mock_openai.embeddings.create.return_value = _FakeEmbeddingsResponse([fake_vector])

    with patch('apps.code_analyzer.services.embedding_service.AsyncOpenAI', return_value=mock_openai):
        client = EmbeddingClient(api_key='fake-key')
        assert client._client is not None

        # Verify get_embeddings
        embeddings = await client.get_embeddings(['hello'])
        assert len(embeddings) == 1
        assert embeddings[0] == fake_vector
        mock_openai.embeddings.create.assert_called_once_with(
            input=['hello'], model='text-embedding-3-small'
        )


@pytest.mark.asyncio
async def test_embedding_service_symbols(tmp_path):
    # Mock dependencies
    mock_db = AsyncMock(spec=AsyncSession)
    mock_client = MagicMock(spec=EmbeddingClient)
    
    fake_vector = [0.2] * 1536
    mock_client.get_embeddings = AsyncMock(return_value=[fake_vector])

    # Mock Symbol object
    mock_symbol = Symbol(
        name='test_func',
        symbol_type='function',
        line_start=1,
        line_end=5,
        signature='def test_func()',
        docstring='test doc',
        content_hash='abc'
    )
    
    # Configure DB return value
    mock_execute_result = MagicMock()
    mock_execute_result.scalars = MagicMock(return_value=MagicMock(all=lambda: [mock_symbol]))
    mock_db.execute.return_value = mock_execute_result

    # Create dummy source file
    dummy_file = tmp_path / 'dummy.py'
    dummy_file.write_text('def test_func():\n    pass\n')

    with patch('apps.code_analyzer.services.embedding_service.EmbeddingClient', return_value=mock_client):
        service = EmbeddingService(mock_db)
        
        count = await service.generate_symbol_embeddings(
            file_id='fake-file-id',
            file_path='dummy.py',
            repo_path=str(tmp_path)
        )
        
        assert count == 1
        assert mock_symbol.embedding == fake_vector
        mock_client.get_embeddings.assert_called_once()
        mock_db.flush.assert_called_once()


@pytest.mark.asyncio
async def test_embedding_service_chunks(tmp_path):
    # Mock dependencies
    mock_db = AsyncMock(spec=AsyncSession)
    mock_client = MagicMock(spec=EmbeddingClient)
    
    fake_vector = [0.3] * 1536
    mock_client.get_embeddings = AsyncMock(side_effect=lambda texts: [fake_vector for _ in texts])

    # Create dummy text file
    dummy_file = tmp_path / 'readme.md'
    dummy_file.write_text('Line 1\nLine 2\n' * 30)  # 60 lines total

    with patch('apps.code_analyzer.services.embedding_service.EmbeddingClient', return_value=mock_client):
        service = EmbeddingService(mock_db)
        
        count = await service.generate_file_chunks(
            file_id='fake-file-id',
            file_path='readme.md',
            repo_path=str(tmp_path)
        )
        
        # 60 lines with 50 chunk size & 10 overlap should produce 2 chunks
        # Chunk 1: lines 0 to 50
        # Chunk 2: lines 40 to 60
        assert count == 2
        assert mock_db.add.call_count == 2
        mock_db.flush.assert_called_once()


@pytest.mark.asyncio
async def test_embedding_service_batch_processing(tmp_path):
    mock_db = AsyncMock(spec=AsyncSession)
    mock_client = MagicMock(spec=EmbeddingClient)
    
    fake_vector = [0.4] * 1536
    mock_client.get_embeddings = AsyncMock(side_effect=lambda texts: [fake_vector for _ in texts])

    # 1. Test create_empty_file_chunks
    # Create dummy file
    dummy_file = tmp_path / 'empty_readme.md'
    dummy_file.write_text('Some non-python content lines\n' * 5)
    
    with patch('apps.code_analyzer.services.embedding_service.EmbeddingClient', return_value=mock_client):
        service = EmbeddingService(mock_db)
        
        # Test empty chunks creation
        chunk_count = await service.create_empty_file_chunks(
            file_id='fake-file-id-empty',
            file_path='empty_readme.md',
            repo_path=str(tmp_path)
        )
        assert chunk_count == 1
        assert mock_db.add.call_count == 1
        mock_db.flush.assert_called_once()
        
        # 2. Test embed_pending_symbols_and_chunks
        # Mocking Symbol and FileChunk rows
        mock_symbol = Symbol(
            name='batch_func',
            symbol_type='function',
            line_start=1,
            line_end=2,
            content_hash='xyz',
            is_embedded=False
        )
        mock_chunk = FileChunk(
            file_id='fake-file-id-empty',
            chunk_index=0,
            content='File: empty_readme.md (chunk 0)\nContent:\nSome non-python content lines',
            is_embedded=False
        )
        
        # We need mock DB responses
        # First query for symbols: (Symbol, IndexedFile.path, Repository.id)
        mock_symbol_result = MagicMock()
        mock_symbol_result.all = MagicMock(return_value=[(mock_symbol, 'dummy.py', 'repo-123')])
        
        # Second query for chunks
        mock_chunk_result = MagicMock()
        mock_chunk_result.scalars = MagicMock(return_value=MagicMock(all=lambda: [mock_chunk]))
        
        # Side effect for db.execute: symbols first, then chunks
        db_exec_side_effects = [mock_symbol_result, mock_chunk_result]
        mock_db.execute.side_effect = db_exec_side_effects

        # Patch settings storage path so it points to tmp_path
        with patch('infra.config.settings.repo_storage_path', str(tmp_path)):
            # Write dummy.py to tmp_path / repo-123 / dummy.py
            repo_dir = tmp_path / 'repo-123'
            repo_dir.mkdir(parents=True, exist_ok=True)
            (repo_dir / 'dummy.py').write_text('def batch_func():\n    pass\n')
            
            stats = await service.embed_pending_symbols_and_chunks(limit=10)
            
            assert stats['symbols'] == 1
            assert stats['chunks'] == 1
            assert mock_symbol.embedding == fake_vector
            assert mock_symbol.is_embedded is True
            assert mock_chunk.embedding == fake_vector
            assert mock_chunk.is_embedded is True
