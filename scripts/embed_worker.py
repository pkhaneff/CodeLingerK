import asyncio
import logging
import sys
from pathlib import Path

# Add root folder to sys.path
sys.path.append(str(Path(__file__).parent.parent))

# Ensure all SQLAlchemy models are imported for mapper resolution
from apps.auth.models.user import User
from apps.auth.models.role import Role
from apps.auth.models.blacklisted_token import BlacklistedToken
from apps.repositories.models.repository import Repository
from apps.ai_reviewer.models.pull_request import PullRequest
from apps.ai_reviewer.models.snapshot import Snapshot
from apps.ai_reviewer.models.review import Review, ReviewComment
from apps.code_analyzer.models.code_graph import (
    IndexedFile,
    Symbol,
    FileChunk,
    SymbolCall,
    SymbolImport,
    SymbolInheritance,
)

from infra.database import get_db_context
from apps.code_analyzer.services.embedding_service import EmbeddingService

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s [%(name)s:%(lineno)d] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("embed_worker")

async def main():
    logger.info("Starting Embedding Worker (one-off execution)...")
    total_symbols = 0
    total_chunks = 0
    
    while True:
        try:
            async with get_db_context() as db:
                embed_svc = EmbeddingService(db)
                stats = await embed_svc.embed_pending_symbols_and_chunks(limit=50)
                if stats['symbols'] > 0 or stats['chunks'] > 0:
                    logger.info(
                        f"Processed batch: embedded {stats['symbols']} symbols and "
                        f"{stats['chunks']} file chunks."
                    )
                    total_symbols += stats['symbols']
                    total_chunks += stats['chunks']
                else:
                    break
        except Exception as e:
            logger.error(f"Error in Embedding Worker execution: {e}", exc_info=True)
            sys.exit(1)

    logger.info(f"Embedding process finished. Total embedded: {total_symbols} symbols, {total_chunks} chunks.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Embedding Worker daemon stopped by user.")
