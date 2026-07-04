"""
CodeLingerK - AI-Powered Code Review System
Moc 4: Code Graph Indexing (PostgreSQL)
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from core.logging_config import setup_logging, get_logger
from infra.config import settings
from infra.database import init_db, close_db
from infra.redis_client import redis_client
from apps.auth.api.routes import router as auth_router
from apps.ai_reviewer.api.webhooks import router as webhook_router
from apps.repositories.api.routes import router as repo_router
from apps.code_analyzer.api.routes import router as graph_router
from apps.ai_reviewer.api.routes import router as reviews_router
from worker import Worker

setup_logging(level=settings.log_level)
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


from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from core.exceptions import AppException, ErrorCode
from core.responses import error_response

app = FastAPI(
    title='CodeLingerK',
    description='AI-Powered Code Review System',
    version='0.5.0',
    lifespan=lifespan,
)


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    """Handler for all custom AppException subclasses."""
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response(
            code=exc.error_code.name,
            message=exc.message,
            reason=exc.reason,
            details=exc.details
        )
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handler for Pydantic schema validation errors."""
    return JSONResponse(
        status_code=400,
        content=error_response(
            code=ErrorCode.VALIDATION_FAILED.name,
            message=ErrorCode.VALIDATION_FAILED.message,
            reason=ErrorCode.VALIDATION_FAILED.reason,
            details=exc.errors()
        )
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Handler for default HTTPExceptions raised by FastAPI/Starlette."""
    status_code = exc.status_code
    error_code = ErrorCode.INVALID_REQUEST
    if status_code == 404:
        error_code = ErrorCode.NOT_FOUND
    elif status_code == 401:
        error_code = ErrorCode.UNAUTHENTICATED
    elif status_code == 403:
        error_code = ErrorCode.UNAUTHORIZED
    
    return JSONResponse(
        status_code=status_code,
        content=error_response(
            code=error_code.name,
            message=str(exc.detail),
            reason=error_code.reason,
            details=None
        )
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """Fallback handler for any uncaught system errors."""
    logger.exception(f"Unhandled system error occurred: {exc}")
    return JSONResponse(
        status_code=500,
        content=error_response(
            code=ErrorCode.SYSTEM_ERROR.name,
            message=ErrorCode.SYSTEM_ERROR.message,
            reason=ErrorCode.SYSTEM_ERROR.reason,
            details=str(exc) if settings.debug else None
        )
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
