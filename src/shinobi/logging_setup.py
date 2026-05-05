"""Configuration de structlog avec sortie console rich + fichier JSON.

Console : pretty (rich) si LOG_CONSOLE_PRETTY=true, sinon JSON line.
Fichier : JSON ligne par ligne, append-only.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

import structlog

from shinobi.config import settings

_configured = False


def configure_logging() -> None:
    """Configure structlog. Idempotent."""
    global _configured
    if _configured:
        return

    log_path = settings.log_file_full_path
    log_path.parent.mkdir(parents=True, exist_ok=True)

    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]

    if settings.log_console_pretty:
        console_renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer(
            colors=False,
        )
    else:
        console_renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            console_renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.WriteLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    _setup_file_logger(log_path, level)
    _configured = True


def _setup_file_logger(log_path: Path, level: int) -> None:
    """Configure un FileHandler stdlib pour la sortie JSON sur disque."""
    root = logging.getLogger()
    root.setLevel(level)

    has_file_handler = any(
        isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers
    )
    if has_file_handler:
        return

    handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter(
            fmt='{"ts":"%(asctime)s","lvl":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
            datefmt="%Y-%m-%dT%H:%M:%SZ",
        )
    )
    handler.setLevel(level)
    root.addHandler(handler)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Retourne un logger structlog configure."""
    if not _configured:
        configure_logging()
    return structlog.get_logger(name)
