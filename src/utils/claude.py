"""
Claude API Utilities

Provides a thin Claude-backed classifier used as a SECONDARY fallback by the
intent and sentiment agents. The primary keyword/rule classifier always runs
first; this client is only invoked when the primary pass produces a
no-confident-match sentinel.

Model: configurable via CLAUDE_CLASSIFIER_MODEL env var (default
`claude-haiku-4-5-20251001` — fast and cheap, which is appropriate for
single-label classification with a tight JSON output contract).
"""

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional


try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False
    logging.warning("anthropic SDK not installed. Claude classifier fallback will be disabled.")


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


DEFAULT_MODEL = "claude-haiku-4-5-20251001"


INTENT_SYSTEM_PROMPT = """You are an intent classifier for a customer-support assistant on an e-commerce platform. The customer may write in English, Swahili, or a mix.

Allowed intents (use EXACTLY one of these labels):
- track_order: order/shipping status, tracking, delivery time
- process_return: returning an item, refund, exchange
- account_issues: login problems, password reset, account locked, profile/email changes
- onboarding: new customer getting started, account creation, first login
- greeting: hello/small talk
- close_conversation: customer says they are done
- request_human: explicit request to speak to a human/agent/person
- general_inquiry: anything else, including unclear or out-of-scope

Respond with ONLY a single-line JSON object: {"intent":"<label>","confidence":<float 0-1>}
No Markdown, no extra text."""


SENTIMENT_SYSTEM_PROMPT = """You are a sentiment classifier for a customer-support assistant. The customer may write in English, Swahili, or a mix.

Allowed labels (use EXACTLY one):
- POSITIVE: happy, satisfied, grateful
- NEUTRAL: calm, factual, matter-of-fact
- NEGATIVE: disappointed, unhappy, frustrated (mild)
- ANGRY: hostile, furious, accusatory
- URGENT: time-sensitive, demands immediate attention

Respond with ONLY a single-line JSON object: {"sentiment":"<LABEL>","confidence":<float 0-1>}
No Markdown, no extra text."""


def _parse_json_response(raw: str) -> Optional[Dict[str, Any]]:
    """Best-effort JSON parser tolerating Markdown fences and stray text."""
    if not raw:
        return None
    cleaned = raw.strip()
    if cleaned.startswith('```'):
        # Strip leading fence (```json or ```)
        cleaned = re.sub(r'^```[a-zA-Z0-9_-]*\s*', '', cleaned)
        # Strip trailing fence
        cleaned = re.sub(r'\s*```\s*$', '', cleaned)
        cleaned = cleaned.strip()

    # Try direct parse first
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except (ValueError, TypeError):
        pass

    # Fall back to extracting the first {...} block
    match = re.search(r'\{[^{}]*\}', cleaned)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, TypeError):
            return None
    return None


class ClaudeClient:
    """Client for the Claude API, scoped to classifier-fallback use.

    Returns None from any classify_* method on:
      - missing ANTHROPIC_API_KEY
      - anthropic SDK not importable
      - API exception
      - unparseable JSON response

    The caller decides how to handle None (escalate, keep rule-based sentinel,
    etc.).
    """

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or os.getenv('ANTHROPIC_API_KEY')
        self.model = model or os.getenv('CLAUDE_CLASSIFIER_MODEL', DEFAULT_MODEL)
        self.client = None

        if not self.api_key:
            logger.warning("No ANTHROPIC_API_KEY found. Claude classifier fallback disabled.")
            return

        if not ANTHROPIC_AVAILABLE:
            logger.warning("anthropic SDK not installed. Install with: pip install anthropic")
            return

        try:
            self.client = anthropic.Anthropic(api_key=self.api_key)
            logger.info(f"Claude classifier client initialized ({self.model})")
        except Exception as e:
            logger.error(f"Failed to initialize Claude client: {e}")

    def classify_intent(
        self,
        text: str,
        history: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Classify the user message into one of the allowed intent labels.

        Returns {'intent': str, 'confidence': float} or None on any failure.
        """
        if not self.client:
            return None

        user_block = ""
        if history:
            recent = [h for h in history[-3:] if h]
            if recent:
                user_block += "RECENT MESSAGES (oldest first):\n"
                user_block += "\n".join(f"- {h}" for h in recent)
                user_block += "\n\n"
        user_block += f'CUSTOMER MESSAGE: "{text}"'

        return self._classify(INTENT_SYSTEM_PROMPT, user_block)

    def classify_sentiment(self, text: str) -> Optional[Dict[str, Any]]:
        """Classify the user message into one of the allowed sentiment labels.

        Returns {'sentiment': str, 'confidence': float} or None on any failure.
        """
        if not self.client:
            return None

        user_block = f'CUSTOMER MESSAGE: "{text}"'
        return self._classify(SENTIMENT_SYSTEM_PROMPT, user_block)

    def _classify(self, system_prompt: str, user_content: str) -> Optional[Dict[str, Any]]:
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=80,
                system=system_prompt,
                messages=[{"role": "user", "content": user_content}],
            )
        except Exception as e:
            logger.warning(f"Claude classifier API error: {e}")
            return None

        raw_text = ""
        try:
            for block in response.content:
                if getattr(block, "type", None) == "text":
                    raw_text += block.text
        except Exception as e:
            logger.warning(f"Claude classifier response parse error: {e}")
            return None

        parsed = _parse_json_response(raw_text)
        if parsed is None:
            logger.warning(f"Claude classifier returned unparseable JSON: {raw_text!r}")
            return None
        return parsed


_global_claude_client: Optional[ClaudeClient] = None


def get_claude_client() -> ClaudeClient:
    """Get the global Claude classifier client singleton."""
    global _global_claude_client
    if _global_claude_client is None:
        _global_claude_client = ClaudeClient()
    return _global_claude_client
