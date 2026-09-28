"""
Centralised logging configuration for SpeakUp.

Call ``setup_logging()`` once at application startup (in app.py).
All other modules use the standard ``logging.getLogger(__name__)`` pattern.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path


def setup_logging(
    level: str = "INFO",
    log_file: str | Path | None = None,
    *,
    fmt: str = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt: str = "%Y-%m-%d %H:%M:%S",
) -> None:
    """
    Configure the root logger with a console handler and optional file handler.

    Args:
        level:    Log level name — ``'DEBUG'``, ``'INFO'``, ``'WARNING'``, etc.
        log_file: Path to a rotating log file.  ``None`` disables file logging.
        fmt:      Log record format string.
        datefmt:  Date/time format string.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    formatter = logging.Formatter(fmt=fmt, datefmt=datefmt)

    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Remove any handlers that were attached before setup_logging() is called
    # (prevents duplicate output during testing or re-initialisation).
    root_logger.handlers.clear()

    # ---- Console handler ------------------------------------------------
    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # ---- Rotating file handler -----------------------------------------
    if log_file is not None:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            filename=log_path,
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    # Quiet noisy third-party loggers in production
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.DEBUG if level.upper() == "DEBUG" else logging.WARNING
    )

    logging.getLogger(__name__).debug(
        "Logging initialised. level=%s  file=%s", level, log_file
    )


def get_logger(name: str) -> logging.Logger:
    """Convenience wrapper — returns a named logger."""
    return logging.getLogger(name)
