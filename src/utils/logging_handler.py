"""Centralized in-memory logging handler for admin dashboard."""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime
from threading import RLock
from typing import Any, Deque, Dict, List, Optional


class InMemoryLogHandler(logging.Handler):
    """Capture Python logs in memory for dashboard consumption."""

    def __init__(self, max_entries: int = 100):
        super().__init__()
        self.max_entries = max_entries
        self._lock = RLock()
        self._logs: Deque[Dict[str, Any]] = deque(maxlen=max_entries)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            module_name = (record.name or "SYSTEM").split(".")[-1].upper()
            log_entry = {
                "timestamp": datetime.utcfromtimestamp(record.created).isoformat(),
                "level": record.levelname,
                "agent": module_name,
                "logger": record.name,
                "message": record.getMessage(),
            }
            with self._lock:
                self._logs.append(log_entry)
        except Exception:
            self.handleError(record)

    def get_logs(
        self,
        *,
        level: Optional[str] = None,
        agent: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            logs = list(self._logs)

        if level and level != "ALL":
            logs = [log for log in logs if log["level"] == level]

        if agent and agent != "ALL":
            normalized = agent.upper()
            logs = [log for log in logs if normalized in (log["agent"], log["logger"].upper())]

        if search:
            search_lower = search.lower()
            logs = [log for log in logs if search_lower in log["message"].lower()]

        logs.reverse()
        return logs[:limit]

    def clear(self) -> None:
        with self._lock:
            self._logs.clear()

    def available_agents(self) -> List[str]:
        with self._lock:
            return sorted({log["agent"] for log in self._logs})

    @property
    def total_logs(self) -> int:
        with self._lock:
            return len(self._logs)


_log_handler: Optional[InMemoryLogHandler] = None


def setup_inmemory_logging(max_entries: int = 100) -> InMemoryLogHandler:
    """Attach one in-memory handler to root logger and return it."""
    global _log_handler

    if _log_handler is not None:
        return _log_handler

    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(level=logging.INFO)

    handler = InMemoryLogHandler(max_entries=max_entries)
    handler.setLevel(logging.INFO)
    root_logger.addHandler(handler)

    _log_handler = handler
    return handler


def get_inmemory_log_handler() -> Optional[InMemoryLogHandler]:
    return _log_handler
