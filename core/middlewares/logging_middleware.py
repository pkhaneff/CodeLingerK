import time
from fastapi import Request
from core.logger import get_logger

logger = get_logger(__name__)


async def logging_middleware(request: Request, call_next):
    start_time = time.time()
    try:
        response = await call_next(request)
        duration = time.time() - start_time
        logger.info(
            "%s %s - %s - %.4fs",
            request.method,
            request.url.path,
            response.status_code,
            duration,
        )
        return response
    except Exception as exc:
        duration = time.time() - start_time
        logger.error(
            "%s %s - ErrorMessage=%s - %.4fs",
            request.method,
            request.url.path,
            str(exc),
            duration,
            exc_info=True,
        )
        raise
