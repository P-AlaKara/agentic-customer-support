#!/usr/bin/env python3
"""
evaluate.py — Summative Evaluation Script
Agentic Customer Support System

Metrics evaluated:
  1. Task Success Rate
  2. Escalation Rate
  3. Average Resolution Time
  4. Intent Recognition Accuracy
  5. Response Quality (LLM-as-Judge via Gemini) - skipped

Usage:
  python evaluate.py                     # default http://localhost:8000
  python evaluate.py --base-url http://...
  python evaluate.py --output my_report.json
  python evaluate.py --skip-quality      # faster, skips Gemini judge

Requirements:
  pip install requests google-generativeai
"""

import argparse
import json
import os
import re
import sys
import time
import statistics
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any

try:
    import requests
except ImportError:
    sys.exit("ERROR: Install 'requests': pip install requests")


# ─────────────────────────────────────────────────────────────────────────────
# Test Dataset
# ─────────────────────────────────────────────────────────────────────────────

# (utterance, expected_intent)  — ground-truth labels for accuracy testing
INTENT_TEST_CASES: List[Tuple[str, str]] = [
    ("Where is my order?",                     "track_order"),
    ("I want to track my order ORD12345",      "track_order"),
    ("When will my package arrive?",           "track_order"),
    ("Has my order shipped yet?",              "track_order"),
    ("What is my order status?",               "track_order"),
    ("I want to return this item",             "process_return"),
    ("How do I get a refund?",                 "process_return"),
    ("I need to send this back",               "process_return"),
    ("Can I exchange this product?",           "process_return"),
    ("I would like to return my laptop",       "process_return"),
    ("How to return goods?",                   "process_return"),
    ("I haven't received my package yet",      "track_order"),
    ("My order is not delivered yet",          "track_order"),
    ("How do I get started?",                  "onboarding"),
    ("I am a new customer, help me set up",    "onboarding"),
    ("How do I create an account?",            "onboarding"),
    ("First time login help",                  "onboarding"),
    ("Hello",                                  "greeting"),
    ("Good morning",                           "greeting"),
    ("Hi there",                               "greeting"),
    ("Goodbye",                                "close_conversation"),
    ("That's all, thanks",                     "close_conversation"),
    ("Bye",                                    "close_conversation"),
    ("I want to speak to a human",             "request_human"),
    ("Connect me to an agent please",          "request_human"),
    ("I need a real person",                   "request_human"),
    ("What are your business hours?",          "general_inquiry"),
    ("Tell me about your return policy",       "general_inquiry"),
    ("What payment methods do you accept?",    "general_inquiry"),
    ("Do you ship internationally?",           "general_inquiry"),
]

# Multi-turn flows.  Each list starts with the wake phrase "hey rehema".
CONVERSATION_FLOWS = [
    {
        "name": "Order Tracking — Happy Path",
        "messages": ["hey rehema", "I want to track my order ORD12345"],
        "expected_outcome": "resolved",
    },
    {
        "name": "Return Request — Happy Path",
        "messages": ["hey rehema", "I want to return my laptop"],
        "expected_outcome": "resolved",
    },
    {
        "name": "Onboarding — Happy Path",
        "messages": ["hey rehema", "I am a new customer, help me get started"],
        "expected_outcome": "resolved",
    },
    {
        "name": "Account Issue — Escalates (no BPA)",
        "messages": ["hey rehema", "I can't log into my account"],
        "expected_outcome": "escalated",  # by design: account_issues → TASK_ESCALATE
    },
    {
        "name": "Manual Human Request — Escalates",
        "messages": ["hey rehema", "I want to speak to a human agent"],
        "expected_outcome": "escalated",
    },
    {
        "name": "General Inquiry — Handled",
        "messages": ["hey rehema", "What are your business hours?"],
        "expected_outcome": "resolved",
    },
    {
        "name": "Multi-Turn: Track then Close",
        "messages": ["hey rehema", "Where is my order?", "That's all, thanks"],
        "expected_outcome": "resolved",
    },
    {
        "name": "Greeting Only",
        "messages": ["hey rehema"],
        "expected_outcome": "resolved",
    },
]

# Response quality test cases — each activates a fresh session
QUALITY_TEST_CASES = [
    ("hey rehema",                                        "initial wake / greeting"),
    ("I want to return my laptop I bought last week",     "return request"),
    ("Where is my order ORD12345?",                      "order tracking"),
    ("I'm a new customer and want to get started",       "onboarding"),
    ("What is your refund policy?",                      "general policy inquiry"),
    ("How long does standard shipping take?",            "shipping info inquiry"),
]


# ─────────────────────────────────────────────────────────────────────────────
# HTTP Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _chat(base_url: str, message: str, session_id: Optional[str] = None,
          email: str = "eval@test.com") -> dict:
    payload = {"message": message, "language": "en", "customer_email": email}
    if session_id:
        payload["session_id"] = session_id
    r = requests.post(f"{base_url}/chat", json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def _agent_events(base_url: str, agent_name: str, limit: int = 15) -> list:
    r = requests.get(f"{base_url}/admin/agent/{agent_name}/events",
                     params={"limit": limit}, timeout=10)
    r.raise_for_status()
    return r.json().get("events", [])


def _analytics(base_url: str) -> dict:
    r = requests.get(f"{base_url}/admin/analytics/conversations", timeout=15)
    r.raise_for_status()
    return r.json()


def _stats(base_url: str) -> dict:
    r = requests.get(f"{base_url}/admin/stats", timeout=10)
    r.raise_for_status()
    return r.json()


# ─────────────────────────────────────────────────────────────────────────────
# Metric 4: Intent Recognition Accuracy
# ─────────────────────────────────────────────────────────────────────────────

def evaluate_intent_accuracy(base_url: str, verbose: bool = True) -> dict:
    """
    For each labeled utterance:
      1. Activate a fresh session with 'hey rehema'.
      2. Send the test utterance on that session.
      3. Query /admin/agent/intent/events and match by session_id to read
         the system's classified intent (this log is never cleared on session
         close, so it is reliable even after the session is finalized).
      4. Compare with the ground-truth label.
    """
    print("\n[Metric 4] Intent Recognition Accuracy")
    print(f"  Test cases : {len(INTENT_TEST_CASES)}")
    print("  " + "-" * 54)

    correct = 0
    records = []

    for utterance, expected in INTENT_TEST_CASES:
        recognized, confidence = "unknown", None
        try:
            wake = _chat(base_url, "hey rehema")
            sid = wake["session_id"]
            _chat(base_url, utterance, session_id=sid)

            # The agent event log stores {input: {session_id, text}, output: {intent, confidence}}
            events = _agent_events(base_url, "intent", limit=20)
            for ev in events:
                if ((ev.get("input") or {}).get("session_id") == sid
                        and ev.get("direction") == "subscribed"):
                    recognized = (ev.get("output") or {}).get("intent", "unknown")
                    confidence = (ev.get("output") or {}).get("confidence")
                    break
        except Exception as exc:
            recognized = f"ERROR:{exc}"

        hit = recognized == expected
        if hit:
            correct += 1
        records.append({"utterance": utterance, "expected": expected,
                         "recognized": recognized, "confidence": confidence,
                         "correct": hit})

        if verbose:
            mark = "+" if hit else "-"
            c_str = f" ({confidence:.2f})" if confidence is not None else ""
            print(f"  [{mark}] '{utterance[:38]:<38}' "
                  f"got={recognized:<22} exp={expected}{c_str}")
        time.sleep(20.0)

    total = len(INTENT_TEST_CASES)
    accuracy = round(correct / total * 100, 2)
    print(f"\n  Result: {correct}/{total}  →  {accuracy}%")
    return {"metric": "Intent Recognition Accuracy",
            "accuracy_pct": accuracy, "correct": correct,
            "total": total, "details": records}


# ─────────────────────────────────────────────────────────────────────────────
# Metrics 1, 2, 3: Task Success / Escalation / Resolution Time
# ─────────────────────────────────────────────────────────────────────────────

def evaluate_conversation_flows(base_url: str, verbose: bool = True) -> dict:
    """
    Runs multi-turn flows, records outcome (resolved / escalated) and
    wall-clock time for each complete flow.
    """
    print("\n[Metrics 1–3] Task Success Rate / Escalation Rate / Resolution Time")
    print(f"  Flows : {len(CONVERSATION_FLOWS)}")
    print("  " + "-" * 54)

    flow_records = []

    for flow in CONVERSATION_FLOWS:
        name, expected = flow["name"], flow["expected_outcome"]
        sid, actual = None, "resolved"
        t0 = time.perf_counter()

        try:
            for message in flow["messages"]:
                resp = _chat(base_url, message, session_id=sid)
                sid = resp["session_id"]
                if resp.get("is_human_handoff") or resp.get("status") == "escalated":
                    actual = "escalated"
                    break
                time.sleep(20.0)
        except Exception as exc:
            actual = f"error:{exc}"

        elapsed = round(time.perf_counter() - t0, 3)
        match = actual == expected
        flow_records.append({"name": name, "expected": expected, "actual": actual,
                              "outcome_correct": match, "resolution_time_s": elapsed,
                              "was_escalated": actual == "escalated"})

        if verbose:
            print(f"  [{'+'if match else'-'}] {name:<44}  {actual:<10}  {elapsed:.2f}s")
        time.sleep(20.0)

    total = len(flow_records)
    resolved_n  = sum(1 for f in flow_records if f["actual"] == "resolved")
    escalated_n = sum(1 for f in flow_records if f["was_escalated"])
    times = [f["resolution_time_s"] for f in flow_records]

    tsr = round(resolved_n  / total * 100, 2)
    esr = round(escalated_n / total * 100, 2)
    avg_t = round(statistics.mean(times), 3)

    print(f"\n  Task Success Rate    : {resolved_n}/{total}  =  {tsr}%")
    print(f"  Escalation Rate      : {escalated_n}/{total}  =  {esr}%")
    print(f"  Avg Resolution Time  : {avg_t}s  "
          f"(min {min(times):.3f}s / max {max(times):.3f}s)")

    return {
        "task_success_rate":   {"metric": "Task Success Rate",   "rate_pct": tsr,
                                "resolved": resolved_n, "total": total},
        "escalation_rate":     {"metric": "Escalation Rate",     "rate_pct": esr,
                                "escalated": escalated_n, "total": total},
        "avg_resolution_time": {"metric": "Average Resolution Time",
                                "avg_seconds": avg_t,
                                "min_seconds": round(min(times), 3),
                                "max_seconds": round(max(times), 3),
                                "sample_size": total},
        "flow_details": flow_records,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Metric 5: Response Quality (LLM-as-Judge)
# ─────────────────────────────────────────────────────────────────────────────

_JUDGE_PROMPT = """\
You are evaluating "Rehema", a customer-support AI for an e-commerce platform.

User message : "{user_msg}"
Context hint : {context}
Bot response : "{bot_response}"

Rate the response. Reply with ONLY a single-line JSON — no markdown, no extra text.

{{"relevance": <1-5>, "correctness": <1-5>, "naturalness": <1-5>, "comment": "<≤12 words>"}}

relevance   : 5 = directly addresses the user, 1 = off-topic
correctness : 5 = factually/procedurally correct for e-commerce support, 1 = wrong
naturalness : 5 = warm, fluent, human-like, 1 = robotic / awkward"""


def evaluate_response_quality(base_url: str, verbose: bool = True) -> dict:
    print("\n[Metric 5] Response Quality (LLM-as-Judge via Gemini)")

    try:
        import google.generativeai as genai
        api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise EnvironmentError("Set GOOGLE_API_KEY or GEMINI_API_KEY in your .env")
        genai.configure(api_key=api_key)
        judge = genai.GenerativeModel(
            "gemini-2.0-flash",
            generation_config={"temperature": 0.0, "max_output_tokens": 120},
        )
        print("  Gemini judge loaded.")
    except Exception as exc:
        print(f"  [SKIP] {exc}")
        return {"metric": "Response Quality", "status": "skipped", "reason": str(exc)}

    print(f"  Test cases : {len(QUALITY_TEST_CASES)}")
    print("  " + "-" * 54)

    records = []
    for user_msg, ctx in QUALITY_TEST_CASES:
        try:
            if re.search(r'\bhey\s+rehema\b', user_msg, re.IGNORECASE):
                resp = _chat(base_url, user_msg)
            else:
                wake = _chat(base_url, "hey rehema")
                resp = _chat(base_url, user_msg, session_id=wake["session_id"])

            bot_response = resp.get("response", "")
            prompt = _JUDGE_PROMPT.format(
                user_msg=user_msg, context=ctx, bot_response=bot_response)
            raw = (judge.generate_content(prompt).text or "").strip()
            raw = re.sub(r'^```(?:json)?|```$', '', raw).strip()
            s = json.loads(raw)
            avg = round((s["relevance"] + s["correctness"] + s["naturalness"]) / 3, 2)
            records.append({"user_message": user_msg, "context": ctx,
                             "bot_response": bot_response[:200],
                             "relevance": s["relevance"],
                             "correctness": s["correctness"],
                             "naturalness": s["naturalness"],
                             "avg_score": avg,
                             "comment": s.get("comment", "")})
            if verbose:
                print(f"  '{user_msg[:38]:<38}'  "
                      f"R={s['relevance']} C={s['correctness']} N={s['naturalness']} "
                      f"avg={avg}  — {s.get('comment','')}")
        except Exception as exc:
            print(f"  [!] '{user_msg[:35]}': {exc}")
            records.append({"user_message": user_msg, "error": str(exc)})
        time.sleep(20.0)

    valid = [r for r in records if "avg_score" in r]
    if valid:
        overall = round(statistics.mean(r["avg_score"] for r in valid), 3)
        avg_r   = round(statistics.mean(r["relevance"]   for r in valid), 3)
        avg_c   = round(statistics.mean(r["correctness"] for r in valid), 3)
        avg_n   = round(statistics.mean(r["naturalness"] for r in valid), 3)
    else:
        overall = avg_r = avg_c = avg_n = 0.0

    print(f"\n  Overall  : {overall}/5  |  Relevance={avg_r}  "
          f"Correctness={avg_c}  Naturalness={avg_n}")
    return {"metric": "Response Quality", "overall_avg_score": overall,
            "avg_relevance": avg_r, "avg_correctness": avg_c,
            "avg_naturalness": avg_n, "cases_evaluated": len(valid),
            "details": records}


# ─────────────────────────────────────────────────────────────────────────────
# Historical Analytics pull from live DB
# ─────────────────────────────────────────────────────────────────────────────

def fetch_historical(base_url: str) -> dict:
    print("\n[Historical] Pulling analytics from system database...")
    try:
        a = _analytics(base_url)
        s = _stats(base_url)
        intent_s = s.get("intent", {})
        total_analyzed = intent_s.get("total_analyzed", 0)
        high_conf = intent_s.get("high_confidence", 0)
        hc_pct = round(high_conf / total_analyzed * 100, 2) if total_analyzed else None

        result = {
            "total_conversations":       a.get("total_conversations"),
            "escalation_rate_pct":       a.get("escalation_rate"),
            "resolution_rate_pct":       a.get("resolution_rate"),
            "avg_duration_s":            a.get("avg_duration_seconds"),
            "avg_messages_per_conv":     a.get("avg_messages_per_conversation"),
            "intent_high_confidence_pct": hc_pct,
            "status_breakdown":          a.get("status_breakdown", {}),
        }
        for k, v in result.items():
            if k != "status_breakdown":
                print(f"  {k:<35}: {v}")
        return result
    except Exception as exc:
        print(f"  [!] {exc}")
        return {"error": str(exc)}


# ─────────────────────────────────────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url",     default="http://localhost:8000")
    parser.add_argument("--output",       default=None)
    parser.add_argument("--skip-quality", action="store_true")
    parser.add_argument("--quiet",        action="store_true")
    args = parser.parse_args()
    verbose = not args.quiet
    base_url = args.base_url.rstrip("/")

    try:
        requests.get(f"{base_url}/admin/health", timeout=5).raise_for_status()
        print(f"Server at {base_url} is healthy.\n")
    except Exception as exc:
        sys.exit(f"Cannot reach server ({exc}).  Start it first:\n"
                 "  python -m src.api.gateway")

    sep = "=" * 60
    print(sep)
    print("  SUMMATIVE EVALUATION — Agentic Customer Support")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(sep)

    results: Dict[str, Any] = {
        "evaluation_timestamp": datetime.now().isoformat(),
        "base_url": base_url,
        "metrics": {},
    }

    intent_result  = evaluate_intent_accuracy(base_url, verbose)
    results["metrics"]["intent_recognition_accuracy"] = intent_result

    flow_result = evaluate_conversation_flows(base_url, verbose)
    results["metrics"]["task_success_rate"]   = flow_result["task_success_rate"]
    results["metrics"]["escalation_rate"]     = flow_result["escalation_rate"]
    results["metrics"]["avg_resolution_time"] = flow_result["avg_resolution_time"]
    results["conversation_flow_details"]      = flow_result["flow_details"]

    if not args.skip_quality:
        print("\n  (Waiting 120s for API quota to refresh before quality evaluation...)")
        time.sleep(120)
        results["metrics"]["response_quality"] = evaluate_response_quality(base_url, verbose)

    results["historical_analytics"] = fetch_historical(base_url)

    m = results["metrics"]
    print(f"\n{sep}")
    print("  FINAL RESULTS")
    print(sep)
    print(f"  {'Metric':<35} {'Value':>10}")
    print("  " + "-" * 47)
    print(f"  {'Task Success Rate':<35} {m['task_success_rate']['rate_pct']:>9.1f}%")
    print(f"  {'Escalation Rate':<35} {m['escalation_rate']['rate_pct']:>9.1f}%")
    print(f"  {'Avg Resolution Time':<35} {m['avg_resolution_time']['avg_seconds']:>9.3f}s")
    print(f"  {'Intent Recognition Accuracy':<35} {m['intent_recognition_accuracy']['accuracy_pct']:>9.1f}%")
    if "response_quality" in m and "overall_avg_score" in m["response_quality"]:
        rq = m["response_quality"]
        print(f"  {'Response Quality (avg /5)':<35} {rq['overall_avg_score']:>9.3f}")
        print(f"    R={rq['avg_relevance']}  C={rq['avg_correctness']}  N={rq['avg_naturalness']}")
    print(sep)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = args.output or f"eval_results_{ts}.json"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2, ensure_ascii=False)
    print(f"\n  Results saved → {out}")


if __name__ == "__main__":
    main()