"""
Persistence layer for critiques and operator decisions on suggested fixes.
"""

import json
import logging
from typing import Any, Dict, List, Optional

try:
    from ..utils.database import get_db_connection, ensure_uuid
    from .schemas import Critique
except (ImportError, ValueError):
    from src.utils.database import get_db_connection, ensure_uuid
    from src.improvement.schemas import Critique


logger = logging.getLogger(__name__)


def write_critique(
    conversation_id: str,
    review_score: int,
    prompt_version: str,
    model: str,
    critique: Optional[Critique],
    raw_response: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> Optional[str]:
    """
    Persist a critique row. Either `critique` is set (success) or `error` is set (failure);
    `raw_response` is the verbatim model output (or error payload) for diagnostics.

    Returns the new critique_id on success, None on failure.
    """
    try:
        from psycopg2.extras import Json
    except ImportError:
        logger.warning("[improvement.store] psycopg2 not available; skipping write")
        return None

    conv_uuid = str(ensure_uuid(conversation_id))
    db = get_db_connection()

    if critique is not None:
        data = {
            "overall_summary": critique.overall_summary,
            "severity": critique.severity,
            "root_cause_agent": critique.root_cause_agent,
            "rating_seems_fair": critique.rating_seems_fair,
            "failure_modes": [fm.model_dump() for fm in critique.failure_modes],
            "policy_violations": [pv.model_dump() for pv in critique.policy_violations],
            "suggested_fixes": [sf.model_dump() for sf in critique.suggested_fixes],
        }
    else:
        data = {
            "overall_summary": None,
            "severity": None,
            "root_cause_agent": None,
            "rating_seems_fair": None,
            "failure_modes": [],
            "policy_violations": [],
            "suggested_fixes": [],
        }

    try:
        with db.get_cursor() as cur:
            cur.execute(
                """INSERT INTO conversation_critiques (
                       conversation_id, review_score, prompt_version, model,
                       overall_summary, severity, root_cause_agent, rating_seems_fair,
                       failure_modes, policy_violations, suggested_fixes,
                       raw_response, error
                   )
                   VALUES (%s, %s, %s, %s,
                           %s, %s, %s, %s,
                           %s, %s, %s,
                           %s, %s)
                   RETURNING critique_id""",
                (
                    conv_uuid, review_score, prompt_version, model,
                    data["overall_summary"], data["severity"], data["root_cause_agent"], data["rating_seems_fair"],
                    Json(data["failure_modes"]), Json(data["policy_violations"]), Json(data["suggested_fixes"]),
                    Json(raw_response) if raw_response is not None else None,
                    error,
                ),
            )
            row = cur.fetchone()
        critique_id = str(row["critique_id"]) if row else None
        logger.info("[improvement.store] wrote critique %s for conv %s", critique_id, conversation_id)
        return critique_id
    except Exception as e:
        logger.error("[improvement.store] failed to write critique for %s: %s", conversation_id, e, exc_info=True)
        return None


def delete_critique(critique_id: str) -> bool:
    """Delete a single critique (and its fix-application log via ON DELETE CASCADE)."""
    db = get_db_connection()
    try:
        with db.get_cursor() as cur:
            cur.execute(
                "DELETE FROM conversation_critiques WHERE critique_id = %s RETURNING critique_id",
                (str(ensure_uuid(critique_id)),),
            )
            row = cur.fetchone()
        return row is not None
    except Exception as e:
        logger.error("[improvement.store] failed to delete critique %s: %s", critique_id, e)
        return False


def bulk_delete_critiques(critique_ids: List[str]) -> int:
    """Delete many critiques by ID. Returns count deleted."""
    if not critique_ids:
        return 0
    db = get_db_connection()
    try:
        uuids = [str(ensure_uuid(cid)) for cid in critique_ids]
        with db.get_cursor() as cur:
            cur.execute(
                "DELETE FROM conversation_critiques WHERE critique_id = ANY(%s)",
                (uuids,),
            )
            return cur.rowcount or 0
    except Exception as e:
        logger.error("[improvement.store] bulk delete failed: %s", e)
        return 0


def mark_fix(critique_id: str, fix_index: int, status: str, note: Optional[str] = None) -> bool:
    """Record operator decision on a single suggested fix. Upserts on (critique_id, fix_index)."""
    if status not in ("applied", "dismissed"):
        raise ValueError(f"Invalid fix status: {status!r}")
    db = get_db_connection()
    try:
        with db.get_cursor() as cur:
            cur.execute(
                """INSERT INTO fix_application_log (critique_id, fix_index, status, note)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (critique_id, fix_index)
                   DO UPDATE SET status = EXCLUDED.status,
                                 note = EXCLUDED.note,
                                 updated_at = NOW()""",
                (str(ensure_uuid(critique_id)), fix_index, status, note),
            )
        return True
    except Exception as e:
        logger.error("[improvement.store] mark_fix failed: %s", e)
        return False


def get_critique(critique_id: str) -> Optional[Dict[str, Any]]:
    db = get_db_connection()
    try:
        with db.get_cursor() as cur:
            cur.execute(
                "SELECT * FROM conversation_critiques WHERE critique_id = %s",
                (str(ensure_uuid(critique_id)),),
            )
            return cur.fetchone()
    except Exception as e:
        logger.error("[improvement.store] get_critique failed: %s", e)
        return None


def get_latest_for_conversation(conversation_id: str) -> Optional[Dict[str, Any]]:
    db = get_db_connection()
    try:
        with db.get_cursor() as cur:
            cur.execute(
                """SELECT * FROM conversation_critiques
                   WHERE conversation_id = %s
                   ORDER BY created_at DESC
                   LIMIT 1""",
                (str(ensure_uuid(conversation_id)),),
            )
            return cur.fetchone()
    except Exception as e:
        logger.error("[improvement.store] get_latest_for_conversation failed: %s", e)
        return None
