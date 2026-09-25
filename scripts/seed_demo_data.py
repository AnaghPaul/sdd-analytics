#!/usr/bin/env python3
"""Seed realistic-looking demo data covering every dashboard feature.

Different from the minimal one-spec script in README.md (that one's a
quick "does the pipeline work" smoke test). This one seeds three specs
in different states, spread over a backdated, chronologically-ordered
timeline, with a small real sleep between posts so a dashboard left
open during the run visibly updates.

Usage: python3 scripts/seed_demo_data.py
Cleanup: docker exec sdd-analytics-postgres-1 psql -U telemetry -d telemetry \
         -c "DELETE FROM events WHERE repository='example-repo';"
"""
import json
import time
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

TELEMETRY_URL = "http://localhost:8000/api/v1/events"
REPOSITORY = "example-repo"
SLEEP_BETWEEN_POSTS = 0.4  # seconds — small, just enough to trickle in on a live dashboard

# Backdated so the most recent event lands a few minutes ago, not "now" —
# looks like a real work session that just wrapped up, not a synthetic batch.
NOW = datetime.now(timezone.utc)
BASE = NOW - timedelta(hours=3)


def post_event(event_type, corr_id, skill, ts, ticket_id=None, branch="feature/demo", metadata=None):
    payload = {
        "event_type": event_type,
        "correlation_id": corr_id,
        "skill_name": f"speckit-{skill}",
        "repository": REPOSITORY,
        "branch": branch,
        "ticket_id": ticket_id,
        "timestamp": ts.isoformat(),
        "metadata": metadata or {},
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        TELEMETRY_URL, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    urllib.request.urlopen(req)
    print(f"  {event_type:<28} {skill:<10} {ts.strftime('%H:%M:%S')}")
    time.sleep(SLEEP_BETWEEN_POSTS)


def run(corr_id, skill, started_at, duration_seconds, tokens, files, ticket_id, branch, summary,
        mcp_usage=None, mode_usage=None):
    """Posts a started/completed pair for one skill run, with the completed
    timestamp offset by the actual duration — not the same instant.

    mode_usage tokens should sum to `tokens`: every turn belongs to exactly
    one permission mode, unlike mcp_usage (only some turns touch an MCP
    server, so that sum can be less than `tokens`)."""
    post_event(
        f"speckit_{skill}_started", corr_id, skill, started_at, ticket_id, branch,
        {"summary": summary, "agent_name": "claude", "agent_version": "claude-sonnet-5", "skill_version": "0.16.5"},
    )
    completed_at = started_at + timedelta(seconds=duration_seconds)
    metadata = {
        "duration_seconds": duration_seconds,
        "tokens_used": tokens,
        "files_considered": files,
        "summary": summary,
        "agent_name": "claude",
        "agent_version": "claude-sonnet-5",
        "skill_version": "0.16.5",
    }
    if mcp_usage:
        metadata["mcp_usage"] = mcp_usage
    if mode_usage:
        metadata["mode_usage"] = mode_usage
    post_event(f"speckit_{skill}_completed", corr_id, skill, completed_at, ticket_id, branch, metadata)
    return completed_at


def main():
    # --- Spec A: DEMO-42 — full lifecycle, done, touches Figma + Atlassian ---
    print("Spec A (DEMO-42) — full lifecycle, figma + atlassian")
    corr_a = str(uuid.uuid4())
    branch_a = "feature/demo-42"
    summary_a = "Implement DEMO-42: add a button that lets support agents reset a customer's password"
    t = run(corr_a, "specify", BASE, 45, 8000, 3, "DEMO-42", branch_a, summary_a)
    t = run(corr_a, "plan", BASE + timedelta(minutes=25), 300, 20000, 6, "DEMO-42", branch_a, summary_a)
    t = run(corr_a, "tasks", BASE + timedelta(minutes=45), 90, 12000, 1, "DEMO-42", branch_a, summary_a)
    run(
        corr_a, "implement", BASE + timedelta(minutes=55), 600, 45000, 14, "DEMO-42", branch_a, summary_a,
        mcp_usage=[
            {"server": "figma", "calls": 5, "tokens": 9000},
            {"server": "atlassian", "calls": 2, "tokens": 3000},
        ],
        mode_usage=[
            {"mode": "auto", "turns": 35, "tokens": 42000},
            {"mode": "plan", "turns": 2, "tokens": 3000},
        ],
    )

    # --- Spec B: DEMO-51 — still in progress, plan re-run once (iteration) ---
    print("Spec B (DEMO-51) — in progress, plan iterated twice, no implement yet")
    corr_b = str(uuid.uuid4())
    branch_b = "feature/demo-51"
    summary_b = "Implement DEMO-51: redesign the tenant onboarding wizard with progressive disclosure"
    run(corr_b, "specify", BASE + timedelta(minutes=90), 60, 7000, 2, "DEMO-51", branch_b, summary_b)
    run(corr_b, "plan", BASE + timedelta(minutes=95), 120, 9000, 4, "DEMO-51", branch_b, summary_b)
    run(
        corr_b, "plan", BASE + timedelta(minutes=110), 240, 22000, 5, "DEMO-51", branch_b, summary_b,
        mode_usage=[
            {"mode": "plan", "turns": 5, "tokens": 15000},
            {"mode": "auto", "turns": 3, "tokens": 7000},
        ],
    )
    run(corr_b, "tasks", BASE + timedelta(minutes=120), 90, 11000, 1, "DEMO-51", branch_b, summary_b)

    # --- Spec C: no ticket mentioned, full lifecycle, light Atlassian touch ---
    print("Spec C (no ticket) — full lifecycle, small atlassian touch")
    corr_c = str(uuid.uuid4())
    branch_c = "feature/dark-mode"
    summary_c = "Add a dark mode toggle to the settings page"
    run(corr_c, "specify", BASE + timedelta(minutes=150), 30, 5000, 2, None, branch_c, summary_c)
    run(corr_c, "plan", BASE + timedelta(minutes=155), 180, 15000, 3, None, branch_c, summary_c)
    run(corr_c, "tasks", BASE + timedelta(minutes=162), 60, 8000, 1, None, branch_c, summary_c)
    run(
        corr_c, "implement", BASE + timedelta(minutes=168), 240, 18000, 5, None, branch_c, summary_c,
        mcp_usage=[{"server": "atlassian", "calls": 1, "tokens": 500}],
        mode_usage=[{"mode": "auto", "turns": 20, "tokens": 18000}],
    )

    print("\nSeeded 3 specs, 24 events, spanning", NOW - BASE, "backdated.")
    print("Open http://localhost:8000/dashboard")
    print("Cleanup: docker exec sdd-analytics-postgres-1 psql -U telemetry -d telemetry "
          "-c \"DELETE FROM events WHERE repository='example-repo';\"")


if __name__ == "__main__":
    main()
