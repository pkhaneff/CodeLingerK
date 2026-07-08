from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.logger import get_logger
from core.exceptions import AppException, ErrorCode
from core.responses import error_response
from infra.config import settings

logger = get_logger(__name__)


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
