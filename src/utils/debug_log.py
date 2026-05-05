"""Temporary debug logging for the Swahili toggle investigation."""

import json
import time
from pathlib import Path
from typing import Any, Dict


DEBUG_SESSION_ID = "c962db"
DEBUG_LOG_PATH = Path(__file__).resolve().parents[2] / "debug-c962db.log"


def agent_debug_log(location: str, message: str, data: Dict[str, Any], hypothesis_id: str) -> None:
    """Append one NDJSON debug entry without affecting the app flow."""
    try:
        payload = {
            "sessionId": DEBUG_SESSION_ID,
            "id": f"log_{int(time.time() * 1000)}",
            "timestamp": int(time.time() * 1000),
            "location": location,
            "message": message,
            "data": data,
            "runId": "sw-toggle-check",
            "hypothesisId": hypothesis_id,
        }
        with DEBUG_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True, default=str) + "\n")
    except Exception:
        return
