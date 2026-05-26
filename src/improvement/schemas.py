"""
Pydantic models for the judge's structured output.

These mirror the JSON schema enforced via Claude tool use in judge.py.
Keep this file as the single source of truth for the critique shape — the
DB columns, dashboard, and judge prompt all derive from it.
"""

from typing import List, Optional, Literal
from pydantic import BaseModel, Field


Severity = Literal["low", "medium", "high", "critical"]
FixConfidence = Literal["low", "medium", "high"]
FixTarget = Literal["prompt", "policy", "routing", "taxonomy"]


class FailureMode(BaseModel):
    """One identified failure, tagged with a taxonomy code."""
    code: str = Field(..., description="Must match a code in failure_mode_taxonomy.")
    at_turn: Optional[int] = Field(None, description="1-indexed turn where the failure manifested.")
    evidence: str = Field(..., description="Quote from the transcript showing the failure.")


class PolicyViolation(BaseModel):
    """An agent response that contradicts a policy doc."""
    policy_file: str = Field(..., description="e.g. 'returns.md'")
    policy_quote: str = Field(..., description="Exact text from the policy that was violated.")
    agent_action_quote: str = Field(..., description="Exact text from the agent that violated it.")
    explanation: str


class SuggestedFix(BaseModel):
    """A concrete, actionable proposal to address one or more failures."""
    target: FixTarget
    target_name: str = Field(..., description="e.g. 'returns_agent', 'returns.md', 'coordinator'.")
    change: str = Field(..., description="The concrete edit to make.")
    rationale: str = Field(..., description="Why this change would prevent the failure.")
    confidence: FixConfidence


class Critique(BaseModel):
    """Top-level critique object — one per (conversation, prompt_version) run."""
    overall_summary: str
    severity: Severity
    root_cause_agent: Optional[str] = Field(
        None,
        description="Name of the agent most responsible. None if root cause is routing/policy/etc.",
    )
    rating_seems_fair: bool = Field(
        ...,
        description="False if the user's poor rating was unrelated to system behavior. "
                    "Critical escape hatch — prevents the pipeline from chasing noise.",
    )
    failure_modes: List[FailureMode] = Field(default_factory=list)
    policy_violations: List[PolicyViolation] = Field(default_factory=list)
    suggested_fixes: List[SuggestedFix] = Field(default_factory=list)
