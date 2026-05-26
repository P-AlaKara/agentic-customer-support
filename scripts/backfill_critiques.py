#!/usr/bin/env python3
"""
Backfill critiques for historical poorly-rated conversations.

Use this for the Phase 2 manual eval step: run the judge against existing
low-rated conversations, then read the resulting critiques and hand-grade
whether they're useful. The judge prompt should be tuned until ≥70% of
critiques look actionable before moving on to Phase 3.

Usage:
    python scripts/backfill_critiques.py                 # all <=3 star convos w/ no critique
    python scripts/backfill_critiques.py --limit 20      # cap at 20
    python scripts/backfill_critiques.py --force         # re-critique even if one exists
    python scripts/backfill_critiques.py --conv-id <id>  # one specific conversation

Reads ANTHROPIC_API_KEY, JUDGE_MODEL, JUDGE_PROMPT_VERSION from .env.
"""

import argparse
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from src.improvement import ingest, judge, store
from src.utils.database import get_db_connection


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("backfill")


def find_target_conversations(limit: int | None, force: bool, single_id: str | None) -> list[tuple[str, int]]:
    db = get_db_connection()
    with db.get_cursor() as cur:
        if single_id:
            cur.execute(
                """SELECT conversation_id, review_score
                   FROM completed_conversations
                   WHERE conversation_id = %s""",
                (single_id,),
            )
        elif force:
            cur.execute(
                """SELECT conversation_id, review_score
                   FROM completed_conversations
                   WHERE review_score IS NOT NULL AND review_score <= 3
                   ORDER BY end_time DESC NULLS LAST"""
            )
        else:
            cur.execute(
                """SELECT c.conversation_id, c.review_score
                   FROM completed_conversations c
                   LEFT JOIN conversation_critiques cc
                     ON cc.conversation_id = c.conversation_id
                   WHERE c.review_score IS NOT NULL
                     AND c.review_score <= 3
                     AND cc.critique_id IS NULL
                   ORDER BY c.end_time DESC NULLS LAST"""
            )
        rows = cur.fetchall()

    out = [(str(r["conversation_id"]), int(r["review_score"])) for r in rows]
    if limit is not None:
        out = out[:limit]
    return out


def run_one(conv_id: str, review_score: int) -> bool:
    logger.info("--- conv %s (score=%d) ---", conv_id, review_score)
    payload = ingest.build_payload(conv_id)
    if payload is None:
        logger.warning("no payload for %s", conv_id)
        return False

    critique_obj, raw, error = judge.critique(payload)

    critique_id = store.write_critique(
        conversation_id=conv_id,
        review_score=review_score,
        prompt_version=os.getenv("JUDGE_PROMPT_VERSION", "judge-v1"),
        model=os.getenv("JUDGE_MODEL", "claude-sonnet-4-6"),
        critique=critique_obj,
        raw_response=raw,
        error=error,
    )

    if critique_obj is not None:
        logger.info(
            "  -> %s | severity=%s | fair=%s | modes=%d | fixes=%d",
            critique_id,
            critique_obj.severity,
            critique_obj.rating_seems_fair,
            len(critique_obj.failure_modes),
            len(critique_obj.suggested_fixes),
        )
        return True
    else:
        logger.error("  -> %s | ERROR: %s", critique_id, error)
        return False


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--limit", type=int, default=None, help="max conversations to process")
    p.add_argument("--force", action="store_true", help="re-critique even if a critique already exists")
    p.add_argument("--conv-id", type=str, default=None, help="critique just this one conversation")
    p.add_argument("--delay", type=float, default=0.5, help="seconds between calls (rate-limit cushion)")
    args = p.parse_args()

    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("ERROR: ANTHROPIC_API_KEY not set in environment")

    targets = find_target_conversations(args.limit, args.force, args.conv_id)
    if not targets:
        print("No conversations to backfill.")
        return

    print(f"Found {len(targets)} conversation(s) to critique.")
    print(f"Model: {os.getenv('JUDGE_MODEL', 'claude-sonnet-4-6')}")
    print(f"Prompt version: {os.getenv('JUDGE_PROMPT_VERSION', 'judge-v1')}")
    print()

    successes = 0
    for i, (conv_id, score) in enumerate(targets, start=1):
        print(f"[{i}/{len(targets)}]", end=" ")
        if run_one(conv_id, score):
            successes += 1
        if i < len(targets):
            time.sleep(args.delay)

    print()
    print(f"Done: {successes}/{len(targets)} successful.")
    print("Review critiques with:")
    print("  SELECT critique_id, conversation_id, severity, root_cause_agent, rating_seems_fair,")
    print("         overall_summary FROM conversation_critiques ORDER BY created_at DESC;")


if __name__ == "__main__":
    main()
