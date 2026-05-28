"""
Aggregations powering the Quality Insights dashboard.

All queries are scoped to a rolling N-day window on `conversation_critiques.created_at`
(default 30). Charts read directly from this; no caching layer — the volume of
critiques is small (one per poor rating) so a fresh SQL pass is fine.
"""

import logging
from typing import Any, Dict, List, Optional

try:
    from ..utils.database import get_db_connection
except (ImportError, ValueError):
    from src.utils.database import get_db_connection


logger = logging.getLogger(__name__)


def _interval(days: int) -> str:
    days = max(1, int(days))
    return f"{days} days"


def headline_stats(days: int = 30) -> Dict[str, Any]:
    """Top-of-page KPIs.

    Returns:
        rated_conversations: count of conversations in window that have a rating
        avg_rating:           mean review_score across those
        bad_rated_count:      count of conversations with review_score <= 3
        critique_count:       total critiques in window (excludes errored)
        errored_count:        critiques where the judge errored
        unfair_count:         critiques where the judge flagged rating_seems_fair=false
        unfair_pct:           unfair_count / critique_count
    """
    db = get_db_connection()
    interval = _interval(days)

    with db.get_cursor() as cur:
        # Conversation-side stats (rating mix in the window)
        cur.execute(
            f"""SELECT
                  COUNT(*) FILTER (WHERE review_score IS NOT NULL)         AS rated_conversations,
                  AVG(review_score) FILTER (WHERE review_score IS NOT NULL) AS avg_rating,
                  COUNT(*) FILTER (WHERE review_score IS NOT NULL AND review_score <= 3) AS bad_rated_count
                FROM completed_conversations
                WHERE end_time >= NOW() - INTERVAL '{interval}'"""
        )
        conv_row = cur.fetchone() or {}

        # Critique-side stats
        cur.execute(
            f"""SELECT
                  COUNT(*)                                              AS total_count,
                  COUNT(*) FILTER (WHERE error IS NOT NULL)             AS errored_count,
                  COUNT(*) FILTER (WHERE rating_seems_fair = false)     AS unfair_count
                FROM conversation_critiques
                WHERE created_at >= NOW() - INTERVAL '{interval}'"""
        )
        cri_row = cur.fetchone() or {}

    total = int(cri_row.get("total_count") or 0)
    errored = int(cri_row.get("errored_count") or 0)
    successful = total - errored
    unfair = int(cri_row.get("unfair_count") or 0)
    unfair_pct = (unfair / successful * 100) if successful > 0 else 0.0

    avg = conv_row.get("avg_rating")
    return {
        "days": days,
        "rated_conversations": int(conv_row.get("rated_conversations") or 0),
        "avg_rating": float(avg) if avg is not None else None,
        "bad_rated_count": int(conv_row.get("bad_rated_count") or 0),
        "critique_count": successful,
        "errored_count": errored,
        "unfair_count": unfair,
        "unfair_pct": round(unfair_pct, 1),
    }


def top_failure_modes(days: int = 30, limit: int = 10) -> List[Dict[str, Any]]:
    """Failure-mode codes ordered by frequency over the window."""
    db = get_db_connection()
    interval = _interval(days)

    with db.get_cursor() as cur:
        cur.execute(
            f"""SELECT
                  fm->>'code'         AS code,
                  COUNT(*)            AS count,
                  COUNT(DISTINCT cc.critique_id) AS critique_count
                FROM conversation_critiques cc,
                     jsonb_array_elements(cc.failure_modes) AS fm
                WHERE cc.created_at >= NOW() - INTERVAL '{interval}'
                  AND cc.failure_modes IS NOT NULL
                  AND cc.error IS NULL
                GROUP BY fm->>'code'
                ORDER BY count DESC
                LIMIT %s""",
            (limit,),
        )
        return [{"code": r["code"], "count": int(r["count"]), "critique_count": int(r["critique_count"])}
                for r in cur.fetchall()]


def root_cause_agents(days: int = 30) -> List[Dict[str, Any]]:
    """Distribution of root-cause-agent assignments over the window."""
    db = get_db_connection()
    interval = _interval(days)

    with db.get_cursor() as cur:
        cur.execute(
            f"""SELECT root_cause_agent AS agent, COUNT(*) AS count
                FROM conversation_critiques
                WHERE created_at >= NOW() - INTERVAL '{interval}'
                  AND root_cause_agent IS NOT NULL
                  AND error IS NULL
                GROUP BY root_cause_agent
                ORDER BY count DESC"""
        )
        return [{"agent": r["agent"], "count": int(r["count"])} for r in cur.fetchall()]


def severity_trend(days: int = 30) -> List[Dict[str, Any]]:
    """Daily critique counts broken down by severity (for stacked bar/area chart)."""
    db = get_db_connection()
    interval = _interval(days)

    with db.get_cursor() as cur:
        cur.execute(
            f"""SELECT
                  DATE_TRUNC('day', created_at)::date AS day,
                  COALESCE(severity, 'unknown') AS severity,
                  COUNT(*) AS count
                FROM conversation_critiques
                WHERE created_at >= NOW() - INTERVAL '{interval}'
                  AND error IS NULL
                GROUP BY day, severity
                ORDER BY day"""
        )
        return [{"day": r["day"].isoformat(), "severity": r["severity"], "count": int(r["count"])}
                for r in cur.fetchall()]


def fix_clusters(days: int = 30, limit: int = 50) -> List[Dict[str, Any]]:
    """Group suggested fixes by (target, target_name).

    The dashboard's biggest payoff: when ten critiques all want the same
    edit to returns_agent's prompt, this clusters them so the operator
    can act once.
    """
    db = get_db_connection()
    interval = _interval(days)

    with db.get_cursor() as cur:
        # First, the cluster sizes
        cur.execute(
            f"""SELECT
                  fx->>'target'                    AS target,
                  fx->>'target_name'               AS target_name,
                  COUNT(*)                         AS suggestion_count,
                  COUNT(DISTINCT cc.critique_id)   AS critique_count
                FROM conversation_critiques cc,
                     jsonb_array_elements(cc.suggested_fixes) AS fx
                WHERE cc.created_at >= NOW() - INTERVAL '{interval}'
                  AND cc.suggested_fixes IS NOT NULL
                  AND cc.error IS NULL
                GROUP BY target, target_name
                ORDER BY suggestion_count DESC
                LIMIT %s""",
            (limit,),
        )
        clusters = [
            {
                "target": r["target"],
                "target_name": r["target_name"],
                "suggestion_count": int(r["suggestion_count"]),
                "critique_count": int(r["critique_count"]),
                "applied_count": 0,
                "dismissed_count": 0,
            }
            for r in cur.fetchall()
        ]
        if not clusters:
            return clusters

        # Layer in applied/dismissed counts per cluster
        cur.execute(
            f"""SELECT
                  fx->>'target' AS target,
                  fx->>'target_name' AS target_name,
                  fal.status AS status,
                  COUNT(*) AS count
                FROM conversation_critiques cc
                JOIN LATERAL jsonb_array_elements(cc.suggested_fixes)
                     WITH ORDINALITY AS t(fx, ord) ON TRUE
                JOIN fix_application_log fal
                     ON fal.critique_id = cc.critique_id
                    AND fal.fix_index = (t.ord - 1)::int
                WHERE cc.created_at >= NOW() - INTERVAL '{interval}'
                  AND cc.error IS NULL
                GROUP BY target, target_name, fal.status"""
        )
        decisions = cur.fetchall()

    by_key = {(c["target"], c["target_name"]): c for c in clusters}
    for d in decisions:
        key = (d["target"], d["target_name"])
        cluster = by_key.get(key)
        if not cluster:
            continue
        status = d["status"]
        if status == "applied":
            cluster["applied_count"] = int(d["count"])
        elif status == "dismissed":
            cluster["dismissed_count"] = int(d["count"])
    return clusters


def fix_cluster_detail(target: str, target_name: str, days: int = 30) -> List[Dict[str, Any]]:
    """Individual suggestions inside one (target, target_name) cluster."""
    db = get_db_connection()
    interval = _interval(days)

    with db.get_cursor() as cur:
        cur.execute(
            f"""SELECT
                  cc.critique_id,
                  cc.conversation_id,
                  cc.created_at,
                  cc.severity,
                  cc.review_score,
                  (t.ord - 1)::int AS fix_index,
                  t.fx->>'change'     AS change,
                  t.fx->>'rationale'  AS rationale,
                  t.fx->>'confidence' AS confidence,
                  fal.status          AS decision_status,
                  fal.note            AS decision_note
                FROM conversation_critiques cc
                JOIN LATERAL jsonb_array_elements(cc.suggested_fixes)
                     WITH ORDINALITY AS t(fx, ord) ON TRUE
                LEFT JOIN fix_application_log fal
                       ON fal.critique_id = cc.critique_id
                      AND fal.fix_index = (t.ord - 1)::int
                WHERE cc.created_at >= NOW() - INTERVAL '{interval}'
                  AND cc.error IS NULL
                  AND t.fx->>'target' = %s
                  AND t.fx->>'target_name' = %s
                ORDER BY cc.created_at DESC""",
            (target, target_name),
        )
        return [
            {
                "critique_id": str(r["critique_id"]),
                "conversation_id": str(r["conversation_id"]),
                "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
                "severity": r.get("severity"),
                "review_score": r.get("review_score"),
                "fix_index": r["fix_index"],
                "change": r.get("change"),
                "rationale": r.get("rationale"),
                "confidence": r.get("confidence"),
                "decision_status": r.get("decision_status"),
                "decision_note": r.get("decision_note"),
            }
            for r in cur.fetchall()
        ]


def find_critique_ids(
    errored_only: bool = False,
    unfair_only: bool = False,
    older_than_days: Optional[int] = None,
) -> List[str]:
    """Return critique IDs matching a cleanup filter — used by bulk delete."""
    db = get_db_connection()

    clauses: List[str] = []
    if errored_only:
        clauses.append("error IS NOT NULL")
    if unfair_only:
        clauses.append("rating_seems_fair = false")
    if older_than_days is not None and older_than_days >= 0:
        clauses.append(f"created_at < NOW() - INTERVAL '{int(older_than_days)} days'")

    if not clauses:
        return []

    where = " AND ".join(clauses)
    with db.get_cursor() as cur:
        cur.execute(f"SELECT critique_id FROM conversation_critiques WHERE {where}")
        return [str(r["critique_id"]) for r in cur.fetchall()]
