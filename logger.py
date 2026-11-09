"""Centralized, privacy-conscious console logging for AI-Shark."""
from __future__ import annotations

import logging
import sys
import time
from contextlib import contextmanager
from typing import Generator

from rich.console import Console
from rich.logging import RichHandler


SUCCESS = 25
logging.addLevelName(SUCCESS, "SUCCESS")


class AISharkLogger:
    """Small structured logging facade; callers supply operational metadata only."""

    def __init__(self, name: str = "ai_shark") -> None:
        self.name = name

    @staticmethod
    def configure_logging() -> None:
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        handlers = [h for h in root.handlers if getattr(h, "_ai_shark_handler", False)]
        if handlers:
            # Keep exactly one AI-Shark console handler after Streamlit reruns.
            for handler in handlers[1:]:
                root.removeHandler(handler)
                handler.close()
            return

        handler = RichHandler(
            console=Console(file=sys.stderr, force_terminal=None, color_system="auto"),
            show_time=True,
            show_level=True,
            show_path=False,
            markup=False,
            rich_tracebacks=False,
            log_time_format="[%Y-%m-%d %H:%M:%S]",
        )
        setattr(handler, "_ai_shark_handler", True)
        root.addHandler(handler)

    def get_logger(self, name: str | None = None) -> logging.Logger:
        return logging.getLogger(name or self.name)

    def event(self, event: str, level: int = logging.INFO, **fields: object) -> None:
        # Values are intentionally not serialized generically. The approved
        # metadata vocabulary is scalar and operational, and callers must not
        # pass content, prompts, credentials, or request/response objects.
        safe_fields = " ".join(
            f"{key}={value}" for key, value in fields.items()
            if key in {"correlation_id", "stage", "shark", "phase", "duration_ms",
                       "calls", "outcome", "category", "status", "call_number"}
            and isinstance(value, (str, int, float, bool))
        )
        self.get_logger().log(level, "%s%s", event,
                              f" | {safe_fields}" if safe_fields else "")

    def exception(self, event: str, error_type: type[BaseException] | str = "Error",
                  **fields: object) -> None:
        category = error_type if isinstance(error_type, str) else error_type.__name__
        self.event(event, logging.ERROR, category=category, **fields)

    @contextmanager
    def timed(self, event: str, **fields: object) -> Generator[None, None, None]:
        started = time.perf_counter()
        try:
            yield
        except Exception as exc:
            self.exception(f"{event} failed", type(exc),
                           duration_ms=round((time.perf_counter() - started) * 1000, 1),
                           **fields)
            raise
        else:
            self.event(event, SUCCESS,
                       duration_ms=round((time.perf_counter() - started) * 1000, 1),
                       outcome="success", **fields)


logger = AISharkLogger()
configure_logging = logger.configure_logging
get_logger = logger.get_logger
event = logger.event
exception = logger.exception
timed = logger.timed
