"""
Entry point for the continuous improvement pipeline.

Called from /chat/review when a conversation is rated <= 3 stars. Runs
asynchronously so the user-facing review endpoint returns immediately —
any failure here MUST NOT propagate back to the chat path.
"""

import asyncio
import logging
import os
from typing import Optional

try:
    from . import ingest, judge, store
except (ImportError, ValueError):
    from src.improvement import ingest, judge, store


logger = logging.getLogger(__name__)


BAD_RATING_THRESHOLD = 3


async def enqueue(conversation_id: str, review_score: int) -> Optional[str]:
    """
    Fire-and-forget entry point. Caller does:

        asyncio.create_task(enqueue(conv_id, score))

    Returns the new critique_id on success, None otherwise. Never raises.
    """
    try:
        if review_score is None or review_score > BAD_RATING_THRESHOLD:
            logger.debug("[improvement.trigger] skipping conv %s (score=%s)", conversation_id, review_score)
            return None

        logger.info("[improvement.trigger] enqueued critique for %s (score=%s)", conversation_id, review_score)

        # Heavy work (DB joins, LLM call) runs on a worker thread so we don't
        # block the API event loop.
        return await asyncio.to_thread(_run_critique_sync, conversation_id, review_score)
    except Exception as e:
        logger.error("[improvement.trigger] unexpected failure for %s: %s", conversation_id, e, exc_info=True)
        return None


def _run_critique_sync(conversation_id: str, review_score: int) -> Optional[str]:
    """Synchronous worker — runs in a thread via asyncio.to_thread."""
    prompt_version = os.getenv("JUDGE_PROMPT_VERSION", "judge-v1")
    model = os.getenv("JUDGE_MODEL", "claude-sonnet-4-6")

    payload = ingest.build_payload(conversation_id)
    if payload is None:
        logger.warning("[improvement.trigger] no payload for %s — aborting", conversation_id)
        return None

    critique_obj, raw_response, error = judge.critique(payload)

    return store.write_critique(
        conversation_id=conversation_id,
        review_score=review_score,
        prompt_version=prompt_version,
        model=model,
        critique=critique_obj,
        raw_response=raw_response,
        error=error,
    )
