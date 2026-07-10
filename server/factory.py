import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.logger import configure_logging, get_logger
from core.exceptions import AppException
from core.middlewares import logging_middleware
from core.exception_handlers import (
    app_exception_handler,
    validation_exception_handler,
    http_exception_handler,
    generic_exception_handler,
)

from infra.config import settings
from infra.database import init_db, close_db
from infra.redis_client import redis_client

from apps.auth.api.routes import router as auth_router
from apps.ai_reviewer.api.webhooks import router as webhook_router
from apps.repositories.api.routes import router as repo_router
from apps.code_analyzer.api.routes import router as graph_router
from apps.ai_reviewer.api.routes import router as reviews_router
from apps.activities.api.routes import router as activities_router
from apps.ai_reviewer.worker import Worker

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.

    Handles startup and shutdown events for database connections
    and background worker.
    """
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

    # Start background worker
    worker = Worker(queues=['context', 'layer', 'review', 'publish'], concurrency=1)
    worker_task = asyncio.create_task(worker.run())
    logger.info('Background worker started')

    logger.info('=' * 60)
    logger.info('Server ready!')
    logger.info('API docs: http://localhost:8000/docs')
    logger.info('=' * 60)

    yield

    # Shutdown
    logger.info('Shutting down...')
    worker.shutdown()
    try:
        await asyncio.wait_for(worker_task, timeout=10)
    except asyncio.TimeoutError:
        logger.warning('Worker did not stop gracefully, cancelling')
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
    
    await close_db()
    await redis_client.close()
    logger.info('Shutdown complete')


def create_app() -> FastAPI:
    """FastAPI Application Factory."""
    configure_logging(log_file=getattr(settings, "log_file", None))

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

    # HTTP request logger middleware
    app.middleware("http")(logging_middleware)

    # Exception handlers
    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, generic_exception_handler)

    # Register routers
    app.include_router(auth_router, prefix='/api/v1/auth')
    app.include_router(repo_router, prefix='/api/v1/repositories')
    app.include_router(graph_router, prefix='/api/v1/repositories')
    app.include_router(reviews_router, prefix='/api/v1')
    app.include_router(webhook_router, prefix='/webhook')
    app.include_router(activities_router, prefix='/api/activities')
    app.include_router(activities_router, prefix='/api/v1/activities')

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
        """
        redis_healthy = await redis_client.health_check()

        return {
            'status': 'healthy',
            'services': {
                'postgresql': 'connected',
                'redis': 'connected' if redis_healthy else 'disconnected',
            },
        }

    return app
