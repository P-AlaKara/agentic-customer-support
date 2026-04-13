"""
Conversation Trace Store

Captures every event-bus event per session in real-time, indexed by session_id.
Provides in-memory access for active sessions and DB persistence for completed ones.
"""

import logging
import uuid
from collections import deque
from datetime import datetime
from threading import RLock
from typing import Any, Dict, List, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MAX_EVENTS_PER_SESSION = 500


class TraceStore:
    """Thread-safe, per-session event trace store."""

    def __init__(self):
        self._traces: Dict[str, deque] = {}
        self._lock = RLock()

    def log(
        self,
        session_id: str,
        event_type: str,
        agent_name: Optional[str],
        direction: str,
        payload: Dict[str, Any],
        timestamp: datetime,
        event_id: Optional[str] = None,
    ):
        entry = {
            "event_id": event_id or str(uuid.uuid4()),
            "timestamp": timestamp.isoformat(),
            "event_type": event_type,
            "agent_name": agent_name,
            "direction": direction,
            "payload": _safe_copy(payload),
        }
        with self._lock:
            if session_id not in self._traces:
                self._traces[session_id] = deque(maxlen=MAX_EVENTS_PER_SESSION)
            self._traces[session_id].append(entry)

    def get_trace(self, session_id: str) -> Optional[List[Dict[str, Any]]]:
        with self._lock:
            buf = self._traces.get(session_id)
            if buf is None:
                return None
            return list(buf)

    def finalize(self, session_id: str):
        """Persist the in-memory trace to DB, then drop it from memory."""
        with self._lock:
            buf = self._traces.pop(session_id, None)

        if not buf:
            return

        events = list(buf)
        try:
            try:
                from .utils.database import get_db_connection, ensure_uuid
            except (ImportError, ValueError):
                from src.utils.database import get_db_connection, ensure_uuid
            db_conn = get_db_connection()
            conv_uuid = str(ensure_uuid(session_id))
            self._bulk_insert(db_conn, conv_uuid, events)
            logger.info(f"[TraceStore] Persisted {len(events)} events for {session_id}")
        except Exception as e:
            logger.error(f"[TraceStore] Failed to persist trace for {session_id}: {e}", exc_info=True)

    @staticmethod
    def _bulk_insert(db_conn, conv_uuid: str, events: List[Dict[str, Any]]):
        try:
            from psycopg2.extras import Json
        except ImportError:
            logger.warning("[TraceStore] psycopg2 not available; skipping DB write")
            return

        with db_conn.get_cursor() as cursor:
            for ev in events:
                cursor.execute(
                    """INSERT INTO conversation_events
                       (conversation_id, event_type, agent_name, direction,
                        payload, event_timestamp, event_id)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (
                        conv_uuid,
                        ev["event_type"],
                        ev.get("agent_name"),
                        ev.get("direction", "published"),
                        Json(ev.get("payload")),
                        ev["timestamp"],
                        ev.get("event_id"),
                    ),
                )

    @staticmethod
    def get_trace_from_db(conversation_id: str) -> Optional[List[Dict[str, Any]]]:
        try:
            try:
                from .utils.database import get_db_connection, ensure_uuid
            except (ImportError, ValueError):
                from src.utils.database import get_db_connection, ensure_uuid
            db_conn = get_db_connection()
            conv_uuid = str(ensure_uuid(conversation_id))
        except Exception:
            return None

        try:
            with db_conn.get_cursor() as cursor:
                cursor.execute(
                    """SELECT event_id, event_type, agent_name, direction,
                              payload, event_timestamp
                       FROM conversation_events
                       WHERE conversation_id = %s
                       ORDER BY event_timestamp ASC, id ASC""",
                    (conv_uuid,),
                )
                rows = cursor.fetchall()

            if not rows:
                return None

            return [
                {
                    "event_id": r["event_id"],
                    "timestamp": r["event_timestamp"].isoformat() if r["event_timestamp"] else None,
                    "event_type": r["event_type"],
                    "agent_name": r["agent_name"],
                    "direction": r["direction"],
                    "payload": r["payload"],
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"[TraceStore] DB read failed: {e}", exc_info=True)
            return None


def _safe_copy(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Shallow copy with large fields truncated to keep memory bounded."""
    if not isinstance(payload, dict):
        return {}
    out = {}
    for k, v in payload.items():
        if k in ("audio_base64",) and isinstance(v, str) and len(v) > 200:
            out[k] = v[:80] + "...<truncated>"
        elif isinstance(v, str) and len(v) > 5000:
            out[k] = v[:5000] + "...<truncated>"
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_global_trace_store: Optional[TraceStore] = None


def get_trace_store() -> TraceStore:
    global _global_trace_store
    if _global_trace_store is None:
        _global_trace_store = TraceStore()
    return _global_trace_store
