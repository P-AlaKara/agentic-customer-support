"""
Claude-powered judge.

Uses the Anthropic SDK with:
  - **tool use** to enforce structured JSON output matching the Critique schema
    (more reliable than asking the model for JSON in prose — the SDK validates
    tool input against the schema before we ever see it)
  - **prompt caching** on the static system prompt (rubric + all 3 policy
    files). Identical across every critique call, so after the first request
    of a 5-minute window every subsequent call reads from cache at ~0.1x cost.
  - `effort: medium` — judging is reasoning-heavy but `high` (Sonnet 4.6's
    default) is overkill for the volume we expect. Adaptive thinking is NOT
    enabled because the API rejects `thinking` when `tool_choice` forces a
    specific tool — and we'd rather have guaranteed structured output than
    visible reasoning blocks.

Model: configurable via `JUDGE_MODEL` env var (default `claude-sonnet-4-6`).
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from .schemas import Critique
except (ImportError, ValueError):
    from src.improvement.schemas import Critique


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Static system prompt — loaded once at module init so the cached prefix is
# byte-identical across every critique call.
# ---------------------------------------------------------------------------

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
_POLICIES_DIR = Path(__file__).resolve().parent.parent.parent / "policies"
_POLICY_FILES = sorted(["onboarding.md", "returns.md", "shipping.md"])


def _build_static_system() -> List[Dict[str, Any]]:
    """Return the system blocks to send on every critique call.

    Single block (rubric + all policies concatenated in a deterministic order)
    with cache_control on it so the entire prefix caches.
    """
    rubric = (_PROMPTS_DIR / "judge_system.md").read_text(encoding="utf-8")

    parts = [rubric, "", "---", "", "# Authoritative policies", ""]
    for filename in _POLICY_FILES:
        path = _POLICIES_DIR / filename
        if not path.exists():
            logger.warning("[judge] policy file missing: %s", path)
            continue
        parts.append(f"## {filename}")
        parts.append("")
        parts.append(path.read_text(encoding="utf-8"))
        parts.append("")

    full_text = "\n".join(parts)
    return [{
        "type": "text",
        "text": full_text,
        "cache_control": {"type": "ephemeral"},
    }]


# ---------------------------------------------------------------------------
# Tool schema — must match src/improvement/schemas.py:Critique exactly.
# ---------------------------------------------------------------------------

_TAXONOMY_CODES = [
    "WRONG_INTENT", "LOW_CONFIDENCE_NOT_ESCALATED", "POLICY_IGNORED",
    "POLICY_MISQUOTED", "MISSING_CONTEXT", "WRONG_AGENT_ROUTED",
    "SENTIMENT_MISSED", "INCOMPLETE_ANSWER", "HALLUCINATED_ACTION",
    "TONE_INAPPROPRIATE", "LANGUAGE_CONFUSION", "INFINITE_LOOP",
    "ESCALATION_FAILED", "SLOW_RESPONSE", "OTHER",
]

_RECORD_CRITIQUE_TOOL = {
    "name": "record_critique",
    "description": (
        "Record your structured critique of the conversation. Call this exactly "
        "once. Output ONLY via this tool — do not write free text outside it."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "overall_summary", "severity", "root_cause_agent",
            "rating_seems_fair", "failure_modes", "policy_violations",
            "suggested_fixes",
        ],
        "properties": {
            "overall_summary": {
                "type": "string",
                "description": "2-4 sentence summary of what went wrong (or why the rating is unfair).",
            },
            "severity": {
                "type": "string",
                "enum": ["low", "medium", "high", "critical"],
            },
            "root_cause_agent": {
                "type": ["string", "null"],
                "description": (
                    "Name of the agent most responsible for the failure "
                    "(e.g. 'intent_agent', 'returns_agent', 'coordinator'). "
                    "Null if the root cause is policy/routing/external, not an agent."
                ),
            },
            "rating_seems_fair": {
                "type": "boolean",
                "description": "False if the poor rating was for reasons outside the system's control.",
            },
            "failure_modes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["code", "evidence"],
                    "properties": {
                        "code": {"type": "string", "enum": _TAXONOMY_CODES},
                        "at_turn": {"type": ["integer", "null"]},
                        "evidence": {
                            "type": "string",
                            "description": "Verbatim quote from the transcript showing the failure.",
                        },
                    },
                },
            },
            "policy_violations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["policy_file", "policy_quote", "agent_action_quote", "explanation"],
                    "properties": {
                        "policy_file": {
                            "type": "string",
                            "enum": _POLICY_FILES,
                        },
                        "policy_quote": {
                            "type": "string",
                            "description": "EXACT text from the policy file that was violated.",
                        },
                        "agent_action_quote": {
                            "type": "string",
                            "description": "EXACT text from the agent's response that violated the policy.",
                        },
                        "explanation": {"type": "string"},
                    },
                },
            },
            "suggested_fixes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["target", "target_name", "change", "rationale", "confidence"],
                    "properties": {
                        "target": {
                            "type": "string",
                            "enum": ["prompt", "policy", "routing", "taxonomy"],
                        },
                        "target_name": {
                            "type": "string",
                            "description": (
                                "Specific target: agent name for 'prompt', filename for 'policy', "
                                "'coordinator' for 'routing', proposed code for 'taxonomy'."
                            ),
                        },
                        "change": {
                            "type": "string",
                            "description": "The actual proposed edit text — concrete, not a description.",
                        },
                        "rationale": {"type": "string"},
                        "confidence": {
                            "type": "string",
                            "enum": ["low", "medium", "high"],
                        },
                    },
                },
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Anthropic client — lazy init so importing the module doesn't require a key.
# ---------------------------------------------------------------------------

_client = None
_static_system: Optional[List[Dict[str, Any]]] = None


def _get_client():
    global _client
    if _client is None:
        try:
            import anthropic
        except ImportError as e:
            raise RuntimeError(
                "anthropic SDK not installed — run `pip install anthropic`"
            ) from e
        _client = anthropic.Anthropic()
    return _client


def _get_static_system() -> List[Dict[str, Any]]:
    global _static_system
    if _static_system is None:
        _static_system = _build_static_system()
    return _static_system


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def critique(
    payload: Dict[str, Any],
) -> Tuple[Optional[Critique], Optional[Dict[str, Any]], Optional[str]]:
    """
    Run the judge over a critique-ready payload.

    Returns:
        (critique, raw_response, error)

        - On success:  (Critique instance, raw response dict, None)
        - On failure:  (None, raw response or error dict, error string)
    """
    model = os.getenv("JUDGE_MODEL", "claude-sonnet-4-6")
    system = _get_static_system()
    user_content = json.dumps(payload, default=str, indent=2)

    last_error: Optional[str] = None
    last_raw: Optional[Dict[str, Any]] = None

    for attempt in (1, 2):
        try:
            client = _get_client()
            response = client.messages.create(
                model=model,
                max_tokens=8192,
                system=system,
                tools=[_RECORD_CRITIQUE_TOOL],
                tool_choice={"type": "tool", "name": "record_critique"},
                output_config={"effort": "medium"},
                messages=[{"role": "user", "content": user_content}],
            )
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            logger.warning("[judge] API error on attempt %d: %s", attempt, last_error)
            last_raw = {"attempt": attempt, "error": last_error}
            continue

        raw = _response_to_dict(response)
        last_raw = raw

        tool_input = _extract_tool_input(response)
        if tool_input is None:
            last_error = f"no record_critique tool_use block (stop_reason={response.stop_reason})"
            logger.warning("[judge] %s — attempt %d", last_error, attempt)
            continue

        try:
            critique_obj = Critique.model_validate(tool_input)
        except Exception as e:
            last_error = f"critique failed pydantic validation: {e}"
            logger.warning("[judge] %s", last_error)
            continue

        logger.info(
            "[judge] critique recorded for conv %s — severity=%s, modes=%d, fixes=%d",
            payload.get("conversation_id"),
            critique_obj.severity,
            len(critique_obj.failure_modes),
            len(critique_obj.suggested_fixes),
        )
        return critique_obj, raw, None

    return None, last_raw, last_error


def _extract_tool_input(response) -> Optional[Dict[str, Any]]:
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "record_critique":
            return block.input
    return None


def _response_to_dict(response) -> Dict[str, Any]:
    """Best-effort dump of an Anthropic Message to a JSON-safe dict."""
    try:
        return response.to_dict()
    except Exception:
        pass
    try:
        return json.loads(response.to_json())
    except Exception:
        return {"repr": repr(response)}
