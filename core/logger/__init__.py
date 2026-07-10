import logging
import os
import sys
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Optional

current_snapshot_id: ContextVar[Optional[str]] = ContextVar("current_snapshot_id", default=None)


LOG_FORMAT = (
    "%(asctime)s"
    " | "
    "%(levelname)-7s"
    " | "
    "%(name)s"
    " : "
    "%(message)s"
)

COLOR_LOG_FORMAT = (
    "\033[38;5;244m%(asctime)s\033[0m"
    " | "
    "%(levelname)-7s"
    " | "
    "\033[38;5;214m%(name)s\033[0m"
    " : "
    "\033[38;5;111m%(message)s\033[0m"
)


class AppFormatter(logging.Formatter):
    LEVEL_COLORS = {
        "DEBUG": "\033[38;5;32m",
        "INFO": "\033[38;5;36m",
        "WARNING": "\033[38;5;221m",
        "ERROR": "\033[38;5;196m",
        "CRITICAL": "\033[48;5;196;38;5;231m",
    }

    RESET = "\033[0m"

    def __init__(self, use_color: bool = False):
        super().__init__(COLOR_LOG_FORMAT if use_color else LOG_FORMAT)
        self.use_color = use_color

    def formatTime(self, record, datefmt=None):
        return datetime.fromtimestamp(record.created).strftime(
            "%Y-%m-%d %H:%M:%S.%f"
        )

    def format(self, record):
        if not self.use_color:
            return super().format(record)

        copied_record = logging.makeLogRecord(record.__dict__.copy())
        color = self.LEVEL_COLORS.get(copied_record.levelname, "")
        copied_record.levelname = f"{color}{copied_record.levelname}{self.RESET}"

        return super().format(copied_record)


class RedisLogHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.setFormatter(AppFormatter(use_color=should_use_color()))

    def emit(self, record):
        snapshot_id = current_snapshot_id.get()
        if not snapshot_id:
            return
        try:
            msg = self.format(record)
            import asyncio
            from infra.redis_client import redis_client
            try:
                loop = asyncio.get_running_loop()
                if loop.is_running():
                    loop.create_task(redis_client.publish_run_raw(snapshot_id, msg))
            except RuntimeError:
                pass
        except Exception:
            pass


def get_log_level() -> int:
    log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    return getattr(logging, log_level_name, logging.INFO)


def should_use_color() -> bool:
    if os.getenv("LOG_COLOR", "").lower() in {"0", "false", "no"}:
        return False

    if os.getenv("LOG_COLOR", "").lower() in {"1", "true", "yes"}:
        return True

    return sys.stderr.isatty()


def configure_logging(log_file: Optional[str] = None) -> None:
    log_level = get_log_level()

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(AppFormatter(use_color=should_use_color()))
    root_logger.addHandler(console_handler)

    # Redis handler for streaming logs to UI
    redis_handler = RedisLogHandler()
    redis_handler.setLevel(log_level)
    root_logger.addHandler(redis_handler)


    # File handler (optional)
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(log_level)
        file_handler.setFormatter(AppFormatter(use_color=False))
        root_logger.addHandler(file_handler)

    # Propagate third-party loggers and clear their handlers
    for logger_name in (
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "fastapi",
    ):
        uvicorn_logger = logging.getLogger(logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
        uvicorn_logger.setLevel(log_level)

    # Silence noisy third-party loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("git").setLevel(logging.WARNING)
    logging.getLogger("tree_sitter").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
