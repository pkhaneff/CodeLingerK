import asyncio
from typing import List
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infra.config import settings
from core.logger import get_logger
from apps.code_analyzer.models.code_graph import Symbol, FileChunk, IndexedFile

logger = get_logger(__name__)


class EmbeddingClient:
    """Client for generating text embeddings using OpenAI API."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ):
        # Fallback order for embedding API key:
        # 1. Explicitly passed api_key
        # 2. settings.ai_embedding_api_key
        # 3. settings.openai_api_key (for custom setup when using deepseek for chat)
        # 4. settings.ai_api_key (only if deepseek/custom is not used or it is openai)
        self.api_key = api_key or settings.ai_embedding_api_key or settings.openai_api_key
        self.base_url = base_url or settings.ai_embedding_base_url
        self.model = model or settings.ai_embedding_model or 'text-embedding-3-small'

        # Fallback to general AI API key if embedding key is still not set and provider is openai
        if not self.api_key and settings.ai_provider == 'openai':
            self.api_key = settings.ai_api_key
            if not self.base_url:
                self.base_url = settings.ai_base_url

        if not self.api_key:
            logger.warning(
                'No API key configured for EmbeddingClient. '
                'Embedding generation will be disabled.'
            )
            self._client = None
        else:
            client_kwargs = {'api_key': self.api_key}
            if self.base_url:
                client_kwargs['base_url'] = self.base_url
            self._client = AsyncOpenAI(**client_kwargs)

    async def get_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings for a list of texts.

        Args:
            texts: List of strings to embed.

        Returns:
            List of embedding vectors (list of floats).
        """
        if not self._client or not texts:
            return []

        # Batch requests to prevent rate limits or large payload issues
        # OpenAI recommends max batch size of 2048 for embeddings
        batch_size = 100
        embeddings = []

        for i in range(0, len(texts), batch_size):
            batch = [t for t in texts[i : i + batch_size] if t.strip()]
            if not batch:
                embeddings.extend([[] for _ in range(len(texts[i : i + batch_size]))])
                continue

            try:
                # Call OpenAI embeddings API
                response = await self._client.embeddings.create(
                    input=batch, model=self.model
                )
                # Extract vectors ordered by input index
                batch_embeddings = [
                    data.embedding
                    for data in sorted(response.data, key=lambda d: d.index)
                ]
                embeddings.extend(batch_embeddings)
            except Exception as e:
                logger.error(f'Failed to generate embeddings: {e}')
                # Return empty lists for this batch to avoid crashing the indexer
                embeddings.extend([[] for _ in batch])

        return embeddings

    async def get_embedding(self, text: str) -> List[float]:
        """Generate embedding for a single text."""
        result = await self.get_embeddings([text])
        return result[0] if result else []


class EmbeddingService:
    """Service for managing database code embeddings."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.client = EmbeddingClient()

    async def generate_symbol_embeddings(
        self,
        file_id: str,
        file_path: str,
        repo_path: str,
    ) -> int:
        """
        Generate and save embeddings for all symbols of a file.

        Args:
            file_id: UUID of the file.
            file_path: Relative path of the file.
            repo_path: Local absolute path to the repository directory.

        Returns:
            Number of symbols updated.
        """
        # 1. Fetch all symbols for the file
        stmt = select(Symbol).where(Symbol.file_id == file_id)
        result = await self.db.execute(stmt)
        symbols = result.scalars().all()

        if not symbols:
            return 0

        # 2. Prepare texts to embed
        from pathlib import Path

        full_path = Path(repo_path) / file_path

        raw_code = ''
        if full_path.exists():
            try:
                raw_code = full_path.read_text(encoding='utf-8', errors='ignore')
            except Exception as e:
                logger.warning(
                    f'Could not read source code for embedding at {full_path}: {e}'
                )

        texts_to_embed = []
        symbols_to_update = []

        for symbol in symbols:
            # Extract raw symbol content from file lines
            symbol_code = ''
            if raw_code and symbol.line_start:
                lines = raw_code.splitlines()
                start = max(0, symbol.line_start - 1)
                end = (
                    symbol.line_end
                    if symbol.line_end is not None
                    else len(lines)
                )
                symbol_code = '\n'.join(lines[start:end])

            text = (
                f'File: {file_path}\n'
                f'Symbol Type: {symbol.symbol_type}\n'
                f'Name: {symbol.name}\n'
                f'Signature: {symbol.signature or ""}\n'
                f'Docstring: {symbol.docstring or ""}\n'
                f'Code Content:\n{symbol_code}'
            )

            texts_to_embed.append(text)
            symbols_to_update.append(symbol)

        # 3. Call API to get embeddings
        vectors = await self.client.get_embeddings(texts_to_embed)

        # 4. Save vectors to symbols
        updated_count = 0
        for symbol, vector in zip(symbols_to_update, vectors):
            if vector:
                symbol.embedding = vector
                symbol.is_embedded = True
                updated_count += 1

        await self.db.flush()
        return updated_count

    async def generate_file_chunks(
        self,
        file_id: str,
        file_path: str,
        repo_path: str,
    ) -> int:
        """
        Split a non-structural file into chunks, embed them, and save them.

        Args:
            file_id: UUID of the file.
            file_path: Relative path of the file.
            repo_path: Local absolute path to the repository directory.

        Returns:
            Number of chunks created.
        """
        # Remove any existing chunks for this file
        from sqlalchemy import delete

        await self.db.execute(delete(FileChunk).where(FileChunk.file_id == file_id))

        from pathlib import Path

        full_path = Path(repo_path) / file_path
        if not full_path.exists():
            return 0

        try:
            content = full_path.read_text(encoding='utf-8', errors='ignore')
        except Exception as e:
            logger.warning(f'Could not read file for chunking at {full_path}: {e}')
            return 0

        # Sliding window chunking: 50 lines per chunk, 10 lines overlap
        lines = content.splitlines()
        if not lines:
            return 0

        chunk_size = 50
        overlap = 10
        chunks_data = []
        i = 0
        chunk_idx = 0

        while i < len(lines):
            chunk_lines = lines[i : i + chunk_size]
            chunk_content = '\n'.join(chunk_lines)

            chunks_data.append({
                'index': chunk_idx,
                'content': f'File: {file_path} (chunk {chunk_idx})\nContent:\n{chunk_content}',
            })

            chunk_idx += 1
            i += chunk_size - overlap
            # Prevent infinite loop if overlap >= chunk_size
            if chunk_size - overlap <= 0:
                break

        if not chunks_data:
            return 0

        # Get embeddings
        vectors = await self.client.get_embeddings(
            [c['content'] for c in chunks_data]
        )

        # Create FileChunk records
        created_count = 0
        for data, vector in zip(chunks_data, vectors):
            if vector:
                chunk = FileChunk(
                    file_id=file_id,
                    chunk_index=data['index'],
                    content=data['content'],
                    embedding=vector,
                    is_embedded=True,
                )
                self.db.add(chunk)
                created_count += 1

        await self.db.flush()
        return created_count

    async def create_empty_file_chunks(
        self,
        file_id: str,
        file_path: str,
        repo_path: str,
    ) -> int:
        """Split a file into empty chunks (without embeddings) and save to DB."""
        # Remove any existing chunks for this file
        from sqlalchemy import delete
        await self.db.execute(delete(FileChunk).where(FileChunk.file_id == file_id))

        from pathlib import Path
        full_path = Path(repo_path) / file_path
        if not full_path.exists():
            return 0

        try:
            content = full_path.read_text(encoding='utf-8', errors='ignore')
        except Exception as e:
            logger.warning(f'Could not read file for chunking at {full_path}: {e}')
            return 0

        lines = content.splitlines()
        if not lines:
            return 0

        chunk_size = 50
        overlap = 10
        chunk_idx = 0
        i = 0
        created_count = 0

        while i < len(lines):
            chunk_lines = lines[i : i + chunk_size]
            chunk_content = '\n'.join(chunk_lines)

            chunk = FileChunk(
                file_id=file_id,
                chunk_index=chunk_idx,
                content=f'File: {file_path} (chunk {chunk_idx})\nContent:\n{chunk_content}',
                embedding=None,
                is_embedded=False
            )
            self.db.add(chunk)
            created_count += 1

            chunk_idx += 1
            i += chunk_size - overlap
            if chunk_size - overlap <= 0:
                break

        await self.db.flush()
        return created_count

    async def get_vector_store_stats(self) -> dict[str, int]:
        """Get counts of embedded symbols and file chunks in the vector store."""
        from sqlalchemy import select, func
        from apps.code_analyzer.models.code_graph import Symbol, FileChunk

        symbol_stmt = select(func.count()).select_from(Symbol).where(Symbol.is_embedded == True)
        chunk_stmt = select(func.count()).select_from(FileChunk).where(FileChunk.is_embedded == True)

        symbol_count = (await self.db.execute(symbol_stmt)).scalar() or 0
        chunk_count = (await self.db.execute(chunk_stmt)).scalar() or 0

        return {
            'embedded_symbols': symbol_count,
            'embedded_chunks': chunk_count,
            'total_vectors': symbol_count + chunk_count
        }

    async def embed_pending_symbols_and_chunks(self, limit: int = 100) -> dict[str, int]:
        """
        Scan database for symbols and file chunks that have not been embedded yet,
        generate embeddings in batch, and mark them as embedded.
        """
        from sqlalchemy import select
        from apps.code_analyzer.models.code_graph import Symbol, FileChunk, IndexedFile
        from apps.repositories.models.repository import Repository
        from infra.config import settings

        stats = {'symbols': 0, 'chunks': 0}

        # 1. Embed pending Symbols
        symbol_stmt = (
            select(Symbol, IndexedFile.path, Repository.id)
            .join(IndexedFile, Symbol.file_id == IndexedFile.id)
            .join(Repository, IndexedFile.repository_id == Repository.id)
            .where(Symbol.is_embedded == False)
            .limit(limit)
        )
        symbol_res = await self.db.execute(symbol_stmt)
        symbol_rows = symbol_res.all()

        if symbol_rows:
            from pathlib import Path
            texts_to_embed = []
            symbols_to_update = []
            content_cache = {}

            logger.info(f"Found {len(symbol_rows)} pending symbols to embed.")
            for symbol, file_path, repo_id in symbol_rows:
                cache_key = (repo_id, file_path)
                if cache_key not in content_cache:
                    full_path = Path(settings.repo_storage_path) / str(repo_id) / file_path
                    raw_code = ''
                    if full_path.exists():
                        try:
                            raw_code = full_path.read_text(encoding='utf-8', errors='ignore')
                        except Exception as e:
                            logger.warning(f'Could not read source code for embedding at {full_path}: {e}')
                    content_cache[cache_key] = raw_code
                else:
                    raw_code = content_cache[cache_key]

                symbol_code = ''
                if raw_code and symbol.line_start:
                    lines = raw_code.splitlines()
                    start = max(0, symbol.line_start - 1)
                    end = symbol.line_end if symbol.line_end is not None else len(lines)
                    symbol_code = '\n'.join(lines[start:end])

                text = (
                    f'File: {file_path}\n'
                    f'Symbol Type: {symbol.symbol_type}\n'
                    f'Name: {symbol.name}\n'
                    f'Signature: {symbol.signature or ""}\n'
                    f'Docstring: {symbol.docstring or ""}\n'
                    f'Code Content:\n{symbol_code}'
                )
                logger.info(f"  [Symbol] Embedding {symbol.symbol_type} '{symbol.name}' in file: {file_path} (lines {symbol.line_start}-{symbol.line_end})")
                texts_to_embed.append(text)
                symbols_to_update.append(symbol)

            try:
                vectors = await self.client.get_embeddings(texts_to_embed)
                for symbol, vector in zip(symbols_to_update, vectors):
                    if vector:
                        symbol.embedding = vector
                        logger.info(f"    -> Generated vector preview: {[round(x, 4) for x in vector[:5]]}... (dimensions: {len(vector)})")
                    symbol.is_embedded = True
                    stats['symbols'] += 1
            except Exception as e:
                logger.error(f'Error embedding symbols batch: {e}')

        # 2. Embed pending FileChunks
        chunk_stmt = (
            select(FileChunk, IndexedFile.path)
            .join(IndexedFile, FileChunk.file_id == IndexedFile.id)
            .where(FileChunk.is_embedded == False)
            .limit(limit)
        )
        chunk_res = await self.db.execute(chunk_stmt)
        chunk_rows = chunk_res.all()

        if chunk_rows:
            texts_to_embed = [row[0].content for row in chunk_rows]
            logger.info(f"Found {len(chunk_rows)} pending file chunks to embed.")
            for chunk, file_path in chunk_rows:
                logger.info(f"  [Chunk] Embedding file chunk {chunk.chunk_index} in file: {file_path} (length: {len(chunk.content)} chars)")
            try:
                vectors = await self.client.get_embeddings(texts_to_embed)
                for (chunk, file_path), vector in zip(chunk_rows, vectors):
                    if vector:
                        chunk.embedding = vector
                        logger.info(f"    -> Generated vector preview: {[round(x, 4) for x in vector[:5]]}... (dimensions: {len(vector)})")
                    chunk.is_embedded = True
                    stats['chunks'] += 1
            except Exception as e:
                logger.error(f'Error embedding chunks batch: {e}')

        if stats['symbols'] > 0 or stats['chunks'] > 0:
            await self.db.flush()

        return stats
