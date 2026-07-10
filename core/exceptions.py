from enum import Enum
from typing import Any, List, Dict

class ErrorCode(Enum):
    # =========================================================================
    # BAD REQUEST (400)
    # =========================================================================
    JSON_PARSE_FAILED = (400, 40001, "Lỗi phân tích định dạng JSON gửi lên")
    VALIDATION_FAILED = (400, 40002, "Dữ liệu không vượt qua vòng kiểm tra hợp lệ")
    MANY_ROLES_FAILED = (400, 40003, "Lỗi gán quá nhiều vai trò không hợp lệ")
    INVALID_REQUEST = (400, 40004, "Yêu cầu không hợp lệ")
    MISSING_REFRESH_TOKEN = (400, 40005, "Thiếu Refresh Token")
    MISSING_FILE = (400, 40006, "Thiếu file trong request")
    INVALID_FILE_TYPE = (400, 40007, "Định dạng file không được hỗ trợ")
    INVALID_JSON = (400, 40008, "JSON không hợp lệ")
    INVALID_JSON_STRUCTURE = (400, 40009, "Cấu trúc JSON không đúng định dạng mong đợi")
    CATEGORY_ALREADY_EXISTS = (400, 40010, "Danh mục đã tồn tại")
    CATEGORY_NOT_FOUND = (400, 40011, "Không tìm thấy danh mục")
    INVALID_CATEGORY_ID = (400, 40012, "ID danh mục không hợp lệ")
    INVALID_FILENAME_EXTENSION = (400, 40013, "Phần mở rộng tên file không hợp lệ")
    INVALID_DOWNLOAD_TYPE = (400, 40014, "Loại download không hợp lệ")
    INVALID_FILE_NAME = (400, 40015, "Tên file không hợp lệ")
    INVALID_CONTRACT_TYPE = (400, 40016, "Loại hợp đồng không hợp lệ")
    INVALID_CONTRACT_STATUS = (400, 40017, "Trạng thái hợp đồng không hợp lệ")
    INVALID_CONTRACT_ID = (400, 40018, "ID hợp đồng không hợp lệ")
    INVALID_PARAMETER = (400, 40019, "Tham số truyền vào không hợp lệ")
    CATEGORY_FILE_IN_PROGRESS = (400, 40020, "File danh mục đang trong quá trình xử lý")
    INVALID_CATEGORY_FILE_STATUS = (400, 40021, "Trạng thái file danh mục không hợp lệ")
    DUPLICATE_BIG_CATEGORY_NAME = (400, 40022, "Trùng tên danh mục lớn (Big Category)")
    DUPLICATE_SMALL_CATEGORY_NAME = (400, 40023, "Trùng tên danh mục nhỏ (Small Category)")
    DUPLICATE_SMALL_CATEGORY_NAME_IN_BIG_CATEGORY = (400, 40024, "Trùng tên danh mục nhỏ trong cùng một danh mục lớn")
    DUPLICATE_SMALL_CATEGORY_NAME_IN_FILE = (400, 40025, "Trùng tên danh mục nhỏ trong file")
    INVALID_BIG_CATEGORY_ID = (400, 40026, "ID danh mục lớn không hợp lệ")
    INVALID_SMALL_CATEGORY_ID = (400, 40027, "ID danh mục nhỏ không hợp lệ")
    FILE_EMPTY = (400, 40028, "File trống (dung lượng bằng 0)")
    MISSING_REQUIRED_FIELDS = (400, 40029, "Thiếu trường thông tin bắt buộc")
    FIELD_EMPTY = (400, 40030, "Trường thông tin không được để trống")
    FIELD_NON_NULL = (400, 40031, "Trường thông tin không được phép mang giá trị null")
    FIELD_INVALID_TYPE = (400, 40032, "Kiểu dữ liệu của trường thông tin không hợp lệ")
    EXCEEDS_MAX_LENGTH = (400, 40033, "Độ dài dữ liệu vượt quá mức tối đa cho phép")
    GENERATE_PRESIGN_URL_FAILED = (400, 40034, "Lỗi tạo đường dẫn tải lên/tải xuống S3 (Presigned URL)")
    UPLOAD_FILE_FAILED = (400, 40035, "Lỗi tải file lên hệ thống lưu trữ")
    FIND_SIMILAR_SMALL_CATEGORY_FAILED = (400, 40036, "Lỗi tìm kiếm danh mục nhỏ tương đồng")

    # =========================================================================
    # UNAUTHORIZED (401)
    # =========================================================================
    UNAUTHENTICATED = (401, 40101, "Chưa xác thực")
    EXPIRED = (401, 40102, "Phiên làm việc đã hết hạn")
    INVALID_TOKEN_TYPE = (401, 40103, "Loại token không hợp lệ")
    INVALID_CREDENTIALS = (401, 40104, "Thông tin đăng nhập không chính xác")
    ACCOUNT_DISABLED = (401, 40105, "Tài khoản đã bị vô hiệu hóa")
    INVALID_TOKEN = (401, 40106, "Token không hợp lệ")
    TOKEN_EXPIRED = (401, 40107, "Token đã hết hạn")
    USER_NOT_FOUND = (401, 40108, "Không tìm thấy người dùng")
    INVALID_AUTH_HEADER = (401, 40109, "Tiêu đề Authorization header không hợp lệ")

    # =========================================================================
    # FORBIDDEN (403)
    # =========================================================================
    UNAUTHORIZED = (403, 40301, "Không có quyền truy cập")
    CONTACT_ADMIN = (403, 40302, "Cần liên hệ quản trị viên để cấp quyền")

    # =========================================================================
    # NOT FOUND (404)
    # =========================================================================
    FILE_NOT_FOUND = (404, 40401, "Không tìm thấy file")
    SMALL_CATEGORY_NOT_FOUND = (404, 40402, "Không tìm thấy danh mục nhỏ")
    CONTRACT_NOT_FOUND = (404, 40403, "Không tìm thấy hợp đồng")
    NOT_FOUND = (404, 40404, "Không tìm thấy tài nguyên đường dẫn")
    NOT_MAPPING = (404, 40405, "Chưa được ánh xạ dữ liệu")

    # =========================================================================
    # CONFLICT (409)
    # =========================================================================
    CONTRACT_ALREADY_EXISTS = (409, 40901, "Hợp đồng đã tồn tại")
    CONFLICT = (409, 40902, "Có xung đột dữ liệu xảy ra")
    NOT_EXISTS = (409, 40903, "Tài nguyên không tồn tại")

    # =========================================================================
    # TOO MANY REQUESTS (429)
    # =========================================================================
    TOO_MANY_REQUESTS = (429, 42901, "Yêu cầu quá thường xuyên, vui lòng thử lại sau")

    # =========================================================================
    # INTERNAL SERVER ERROR (500)
    # =========================================================================
    SYSTEM_ERROR = (500, 50001, "Lỗi hệ thống nội bộ phía Server")

    def __init__(self, status_code: int, reason: int, message: str):
        self.status_code = status_code
        self.reason = reason
        self.message = message


class AppException(Exception):
    """Base exception class for CodeLinger custom application errors."""
    def __init__(
        self,
        error_code: ErrorCode,
        message: str | None = None,
        details: Any | None = None,
    ):
        self.error_code = error_code
        self.status_code = error_code.status_code
        self.reason = error_code.reason
        self.message = message or error_code.message
        self.details = details
        super().__init__(self.message)


class ValidationError(AppException):
    """Exception raised when client inputs or validation check fails (HTTP 400)."""
    def __init__(
        self,
        error_code: ErrorCode = ErrorCode.VALIDATION_FAILED,
        message: str | None = None,
        details: Any | None = None,
    ):
        super().__init__(error_code, message, details)


class NotFoundException(AppException):
    """Exception raised when a resource is not found (HTTP 404)."""
    def __init__(
        self,
        error_code: ErrorCode = ErrorCode.NOT_FOUND,
        message: str | None = None,
        details: Any | None = None,
    ):
        super().__init__(error_code, message, details)


class ConflictException(AppException):
    """Exception raised when a resource conflict occurs (HTTP 409)."""
    def __init__(
        self,
        error_code: ErrorCode = ErrorCode.CONFLICT,
        message: str | None = None,
        details: Any | None = None,
    ):
        super().__init__(error_code, message, details)


class UnauthorizedException(AppException):
    """Exception raised when authentication fails (HTTP 401)."""
    def __init__(
        self,
        error_code: ErrorCode = ErrorCode.UNAUTHENTICATED,
        message: str | None = None,
        details: Any | None = None,
    ):
        super().__init__(error_code, message, details)


class ForbiddenException(AppException):
    """Exception raised when access is forbidden (HTTP 403)."""
    def __init__(
        self,
        error_code: ErrorCode = ErrorCode.UNAUTHORIZED,
        message: str | None = None,
        details: Any | None = None,
    ):
        super().__init__(error_code, message, details)


class TooManyRequestsException(AppException):
    """Exception raised when rate limits are exceeded (HTTP 429)."""
    def __init__(
        self,
        retry_after: int,
        limit: int,
        remaining: int,
        error_code: ErrorCode = ErrorCode.TOO_MANY_REQUESTS,
        message: str | None = None,
        details: Any | None = None,
    ):
        self.headers = {
            "Retry-After": str(retry_after),
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": str(remaining),
        }
        super().__init__(error_code, message, details)


# =========================================================================
# Input Validators Helpers
# =========================================================================

def validate_string_field(field_value: Any, max_length: int = 500) -> str:
    """
    Validate a string field:
    1. Field must not be None (FIELD_NON_NULL).
    2. Field must be an instance of str (FIELD_INVALID_TYPE).
    3. Field must not be empty or whitespace only (FIELD_EMPTY).
    4. Length must not exceed max_length (EXCEEDS_MAX_LENGTH).
    """
    if field_value is None:
        raise ValidationError(
            ErrorCode.FIELD_NON_NULL,
            "Trường thông tin không được phép mang giá trị null"
        )
    if not isinstance(field_value, str):
        raise ValidationError(
            ErrorCode.FIELD_INVALID_TYPE,
            "Kiểu dữ liệu của trường thông tin không hợp lệ"
        )
    if not field_value.strip():
        raise ValidationError(
            ErrorCode.FIELD_EMPTY,
            "Trường thông tin không được để trống"
        )
    if len(field_value) > max_length:
        raise ValidationError(
            ErrorCode.EXCEEDS_MAX_LENGTH,
            f"Độ dài dữ liệu vượt quá mức tối đa cho phép ({max_length} ký tự)"
        )
    return field_value


def validate_required_fields(data: Dict[str, Any], required_fields: List[str]) -> None:
    """
    Ensure all fields in required_fields list are present in data dict.
    Otherwise raise ValidationError with MISSING_REQUIRED_FIELDS error code.
    """
    missing = [f for f in required_fields if f not in data]
    if missing:
        raise ValidationError(
            ErrorCode.MISSING_REQUIRED_FIELDS,
            f"Thiếu trường thông tin bắt buộc: {', '.join(missing)}",
            details={"missing_fields": missing}
        )


def validate_unexpected_fields(data: Dict[str, Any], allowed_fields: List[str]) -> None:
    """
    Ensure that data dict does not contain any key outside allowed_fields list.
    Otherwise raise ValidationError.
    """
    unexpected = [f for f in data if f not in allowed_fields]
    if unexpected:
        raise ValidationError(
            ErrorCode.VALIDATION_FAILED,
            f"Phát hiện thuộc tính lạ không được phép: {', '.join(unexpected)}",
            details={"unexpected_fields": unexpected}
        )
