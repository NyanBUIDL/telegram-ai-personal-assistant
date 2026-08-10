from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

import structlog

from .security import redact


def _redact_processor(_logger: object, _method: str, event_dict: dict) -> dict:
    return redact(event_dict)  # type: ignore[return-value]


def configure_logging(level: str, log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "assistant.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    handlers.append(file_handler)
    logging.basicConfig(level=level.upper(), format="%(message)s", handlers=handlers, force=True)
    # The learning poll runs every two seconds. APScheduler's INFO messages would
    # otherwise dominate the rotating log without adding operational value.
    logging.getLogger("apscheduler.scheduler").setLevel(logging.WARNING)
    logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _redact_processor,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
