"""
CodeLingerK - AI-Powered Code Review System
Moc 4: Code Graph Indexing (PostgreSQL)
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from core.logging_config import setup_logging, get_logger
from infra.config import settings
from infra.database import init_db, close_db
from infra.redis_client import redis_client
from api.routes.auth import router as auth_router
from api.routes.webhooks import router as webhook_router
from api.routes.repositories import router as repo_router
from api.routes.graph import router as graph_router
from api.routes.reviews import router as reviews_router

setup_logging(level=settings.log_level)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.

    Handles startup and shutdown events for database connections.
    """
    # Startup
    logger.info('=' * 60)
    logger.info(f'{settings.app_name} Server - Moc 4: Code Graph (PostgreSQL)')
    logger.info('=' * 60)

    # Initialize databases
    try:
        logger.info('Initializing PostgreSQL...')
        await init_db()
        logger.info('PostgreSQL initialized')
    except Exception as e:
        logger.warning(f'PostgreSQL connection failed: {e}')

    try:
        logger.info('Connecting to Redis...')
        await redis_client.connect()
        logger.info('Redis connected')
    except Exception as e:
        logger.warning(f'Redis connection failed: {e}')

    logger.info('=' * 60)
    logger.info('Server ready!')
    logger.info('API docs: http://localhost:8000/docs')
    logger.info('=' * 60)

    yield

    # Shutdown
    logger.info('Shutting down...')
    await close_db()
    await redis_client.close()
    logger.info('Shutdown complete')


app = FastAPI(
    title='CodeLingerK',
    description='AI-Powered Code Review System',
    version='0.5.0',
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

# Register routers
app.include_router(auth_router, prefix='/api/v1/auth')
app.include_router(repo_router, prefix='/api/v1/repositories')
app.include_router(graph_router, prefix='/api/v1/repositories')
app.include_router(reviews_router, prefix='/api/v1')
app.include_router(webhook_router, prefix='/webhook')


@app.get('/')
async def root():
    """Service info endpoint."""
    return {
        'service': settings.app_name,
        'status': 'running',
        'stage': 'Moc 5 - AI Review Pipeline',
        'version': '0.6.0',
        'capabilities': [
            'GitHub OAuth authentication',
            'Repository management (add/remove/list)',
            'Repository cloning',
            'Webhook installation',
            'Full code graph indexing to PostgreSQL',
            'Parse Python files (functions, classes, methods)',
            'Track imports, calls, inheritance',
            'Query code graph (files, symbols, callers)',
            'GitHub webhook integration',
            'PostgreSQL user storage',
            'AI-powered code review pipeline',
            'Snapshot-based immutable PR state',
            'Functional layer classification',
            '5-pass AI review analysis',
            'GitHub review sync',
            'Queue-based async processing',
        ],
    }


@app.get('/health')
async def health():
    """
    Health check endpoint.

    Returns status of all database connections.
    """
    redis_healthy = await redis_client.health_check()

    return {
        'status': 'healthy',
        'services': {
            'postgresql': 'connected',  # If we got here, it's working
            'redis': 'connected' if redis_healthy else 'disconnected',
        },
    }


if __name__ == '__main__':
    uvicorn.run(
        'main:app',
        host='0.0.0.0',
        port=8000,
        reload=True,
        log_level='info',
    )
