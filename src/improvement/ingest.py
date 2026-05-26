"""
Critique payload builder.

Joins data from completed_conversations, completed_messages, and
conversation_events into a single dict that the judge can reason over.

Policy markdown is NOT loaded here — judge.py owns policy loading so the
cached system prompt is byte-identical across critiques (which means
Claude's prompt cache reads on every call after the first).
"""

import logging
from typing import Any, Dict, List, Optional

try:
    from ..utils.database import get_db_connection, ensure_uuid
except (ImportError, ValueError):
    from src.utils.database import get_db_connection, ensure_uuid


logger = logging.getLogger(__name__)


def build_payload(conversation_id: str) -> Optional[Dict[str, Any]]:
    """
    Assemble a critique-ready payload for a single conversation.

    Returns None if the conversation cannot be found or has no messages.
    """
    conv_uuid = str(ensure_uuid(conversation_id))
    db = get_db_connection()

    with db.get_cursor() as cur:
        cur.execute(
            """SELECT conversation_id, start_time, end_time, final_status,
                      review_score, customer_id, operator_id
               FROM completed_conversations
               WHERE conversation_id = %s""",
            (conv_uuid,),
        )
        header = cur.fetchone()

    if not header:
        logger.warning("[ingest] No conversation row for %s", conversation_id)
        return None

    with db.get_cursor() as cur:
        cur.execute(
            """SELECT timestamp, sender, text_content,
                      intent_label, sentiment_label, entities, agent_action
               FROM completed_messages
               WHERE conversation_id = %s
               ORDER BY timestamp ASC, message_id ASC""",
            (conv_uuid,),
        )
        rows = cur.fetchall()

    if not rows:
        logger.warning("[ingest] No messages for conversation %s", conversation_id)
        return None

    messages: List[Dict[str, Any]] = []
    for i, r in enumerate(rows, start=1):
        messages.append({
            "turn": i,
            "timestamp": r["timestamp"].isoformat() if r.get("timestamp") else None,
            "sender": r.get("sender"),
            "text": r.get("text_content"),
            "intent_label": r.get("intent_label"),
            "sentiment_label": r.get("sentiment_label"),
            "entities": r.get("entities"),
            "agent_action": r.get("agent_action"),
        })

    routing_decisions = _extract_routing_decisions(conv_uuid)
    escalations = _extract_escalations(conv_uuid)

    duration_seconds: Optional[float] = None
    if header.get("start_time") and header.get("end_time"):
        duration_seconds = (header["end_time"] - header["start_time"]).total_seconds()

    return {
        "conversation_id": str(header["conversation_id"]),
        "review_score": header.get("review_score"),
        "final_status": header.get("final_status"),
        "duration_seconds": duration_seconds,
        "messages": messages,
        "routing_decisions": routing_decisions,
        "escalations": escalations,
    }


def _extract_routing_decisions(conv_uuid: str) -> List[Dict[str, Any]]:
    """Pull TASK_HANDLE_* publishes from the event trace to reconstruct routing."""
    db = get_db_connection()
    try:
        with db.get_cursor() as cur:
            cur.execute(
                """SELECT event_type, agent_name, payload, event_timestamp
                   FROM conversation_events
                   WHERE conversation_id = %s
                     AND event_type LIKE 'TASK_HANDLE_%%'
                   ORDER BY event_timestamp ASC""",
                (conv_uuid,),
            )
            rows = cur.fetchall()
        return [{
            "at_timestamp": r["event_timestamp"].isoformat() if r.get("event_timestamp") else None,
            "event_type": r["event_type"],
            "from_agent": r.get("agent_name"),
            "payload_summary": _summarize_payload(r.get("payload")),
        } for r in rows]
    except Exception as e:
        logger.debug("[ingest] routing_decisions extraction failed: %s", e)
        return []


def _extract_escalations(conv_uuid: str) -> List[Dict[str, Any]]:
    db = get_db_connection()
    try:
        with db.get_cursor() as cur:
            cur.execute(
                """SELECT event_type, payload, event_timestamp
                   FROM conversation_events
                   WHERE conversation_id = %s
                     AND (event_type = 'TASK_ESCALATE' OR event_type LIKE '%%ESCALAT%%')
                   ORDER BY event_timestamp ASC""",
                (conv_uuid,),
            )
            rows = cur.fetchall()
        return [{
            "at_timestamp": r["event_timestamp"].isoformat() if r.get("event_timestamp") else None,
            "event_type": r["event_type"],
            "reason": (r.get("payload") or {}).get("reason"),
        } for r in rows]
    except Exception as e:
        logger.debug("[ingest] escalations extraction failed: %s", e)
        return []


def _summarize_payload(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Strip large fields from the routing-event payload."""
    if not isinstance(payload, dict):
        return {}
    keep = {}
    for k in ("intent", "intent_label", "confidence", "sentiment", "entities", "reason"):
        if k in payload:
            keep[k] = payload[k]
    return keep
