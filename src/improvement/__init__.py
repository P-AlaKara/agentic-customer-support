"""
Continuous Improvement Pipeline

Runs a Claude-powered judge over poorly-rated conversations (<= 3 stars),
producing structured critiques that surface failure modes, policy
violations, and suggested fixes.

Components:
- ingest:  builds critique-ready payloads from DB
- judge:   runs Claude against the payload (Phase 2)
- store:   persists critiques and fix decisions
- trigger: entry point called from /chat/review
"""
