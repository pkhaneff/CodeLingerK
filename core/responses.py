"""
API response utilities.

Standard response envelope for all API endpoints.
"""

from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar('T')


class ApiResponse(BaseModel, Generic[T]):
    """
    Standard API response envelope.

    All API responses should use this format:
    {
        "success": true,
        "data": <payload>,
        "message": "Optional message"
    }
    """

    success: bool = True
    data: T
    message: str | None = None


class ApiErrorDetail(BaseModel):
    """Error detail structure."""

    code: str
    message: str
    reason: int | None = None
    details: Any | None = None


class ApiErrorResponse(BaseModel):
    """
    Standard API error response.

    {
        "success": false,
        "error": {
            "code": "ERROR_CODE",
            "message": "Human readable message",
            "reason": 40001,
            "details": null
        }
    }
    """

    success: bool = False
    error: ApiErrorDetail


def success_response(data: T, message: str | None = None) -> dict[str, Any]:
    """
    Create a success response envelope.

    Args:
        data: The response payload
        message: Optional message

    Returns:
        Dict with success envelope format
    """
    response = {'success': True, 'data': data}
    if message:
        response['message'] = message
    return response


def error_response(
    code: str,
    message: str,
    reason: int | None = None,
    details: Any | None = None,
) -> dict[str, Any]:
    """
    Create an error response envelope.

    Args:
        code: Error code (e.g., "VALIDATION_ERROR")
        message: Human readable error message
        reason: Optional numeric error code (e.g., 40001)
        details: Optional error details

    Returns:
        Dict with error envelope format
    """
    err_detail = {
        'code': code,
        'message': message,
    }
    if reason is not None:
        err_detail['reason'] = reason
    if details is not None:
        err_detail['details'] = details
        
    return {
        'success': False,
        'error': err_detail,
    }

