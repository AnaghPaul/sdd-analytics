#!/usr/bin/env python3
"""Seed a larger, randomized-but-plausible volume of demo data — enough
for the Grafana trend panels to show an actual trend, not 2-3 sparse
points. Different from seed_demo_data.py (3 curated specs, one clear
story each) and the README's one-spec smoke test.

Spread over the last 14 days, ~20 specs, each stopping at a random
point in the specify->plan->tasks->implement lifecycle (some finish,
some don't) — so the Specs table and trend graphs both show realistic
variety instead of one flat batch.

Usage: python3 scripts/seed_bulk_demo_data.py [--count 20]
Cleanup: docker exec sdd-analytics-postgres-1 psql -U telemetry -d telemetry \
         -c "DELETE FROM events WHERE repository='example-repo';"
"""
import argparse
import json
import random
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

TELEMETRY_URL = "http://localhost:8000/api/v1/events"
REPOSITORY = "example-repo"
WINDOW_DAYS = 14

SKILLS = ["specify", "plan", "tasks", "implement"]
# (min_seconds, max_seconds, min_tokens, max_tokens, min_files, max_files)
RANGES = {
    "specify":   (20, 90, 3000, 12000, 1, 5),
    "plan":      (60, 400, 5000, 30000, 2, 8),
    "tasks":     (30, 150, 3000, 15000, 1, 3),
    "implement": (200, 900, 15000, 60000, 3, 20),
}
SUMMARIES = [
    "Add a button that lets support agents reset a customer's password",
    "Redesign the tenant onboarding wizard with progressive disclosure",
    "Add a dark mode toggle to the settings page",
    "Fix pagination bug in the FAQ admin table",
    "Add bulk-export for tenant configuration",
    "Migrate the chatbot preview to the new design tokens",
    "Add rate limiting to the public API",
    "Improve error messages on the collection editor",
    "Add audit logging for tenant admin actions",
    "Support multiple languages in the FAQ widget",
]
MCP_PROFILES = [
    None, None,  # most runs touch nothing
    [{"server": "figma", "calls": random.randint(2, 8), "tokens": 0}],
    [{"server": "atlassian", "calls": random.randint(1, 4), "tokens": 0}],
    [{"server": "figma", "calls": 4, "tokens": 0}, {"server": "atlassian", "calls": 2, "tokens": 0}],
]


def post_event(payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(TELEMETRY_URL, data=data, headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req)


def make_mode_usage(total_tokens):
    """Mostly auto, occasionally some plan-mode turns — tokens must sum
    to total_tokens exactly, since every turn belongs to one mode."""
    if random.random() < 0.7:
        return [{"mode": "auto", "turns": random.randint(5, 40), "tokens": total_tokens}]
    plan_share = random.uniform(0.1, 0.4)
    plan_tokens = int(total_tokens * plan_share)
    return [
        {"mode": "auto", "turns": random.randint(5, 30), "tokens": total_tokens - plan_tokens},
        {"mode": "plan", "turns": random.randint(1, 5), "tokens": plan_tokens},
    ]


def run_spec(index, base_time):
    corr_id = str(uuid.uuid4())
    ticket_id = f"DEMO-{random.randint(100, 999)}" if random.random() < 0.6 else None
    branch = f"feature/demo-{index}"
    summary = random.choice(SUMMARIES)

    # How far this spec gets: weighted so most reach tasks/implement,
    # some stop early (still "in progress" in the Specs table).
    stop_at = random.choices([1, 2, 3, 4], weights=[10, 15, 20, 55])[0]
    skills_this_run = SKILLS[:stop_at]

    t = base_time
    for skill in skills_this_run:
        lo_s, hi_s, lo_t, hi_t, lo_f, hi_f = RANGES[skill]
        duration = random.randint(lo_s, hi_s)
        tokens = random.randint(lo_t, hi_t)
        files = random.randint(lo_f, hi_f)

        post_event({
            "event_type": f"speckit_{skill}_started",
            "correlation_id": corr_id,
            "skill_name": f"speckit-{skill}",
            "repository": REPOSITORY,
            "branch": branch,
            "ticket_id": ticket_id,
            "timestamp": t.isoformat(),
            "metadata": {
                "summary": summary,
                "agent_name": "claude",
                "agent_version": "claude-sonnet-5",
                "skill_version": "0.16.5",
            },
        })

        completed_at = t + timedelta(seconds=duration)
        metadata = {
            "duration_seconds": duration,
            "tokens_used": tokens,
            "files_considered": files,
            "summary": summary,
            "agent_name": "claude",
            "agent_version": "claude-sonnet-5",
            "skill_version": "0.16.5",
            "mode_usage": make_mode_usage(tokens),
        }
        mcp_profile = random.choice(MCP_PROFILES) if skill == "implement" else None
        if mcp_profile:
            # distribute tokens across the servers touched, roughly
            per_server_tokens = tokens // (len(mcp_profile) * 3)
            mcp_profile = [{**s, "tokens": per_server_tokens * (i + 1)} for i, s in enumerate(mcp_profile)]
            metadata["mcp_usage"] = mcp_profile

        post_event({
            "event_type": f"speckit_{skill}_completed",
            "correlation_id": corr_id,
            "skill_name": f"speckit-{skill}",
            "repository": REPOSITORY,
            "branch": branch,
            "ticket_id": ticket_id,
            "timestamp": completed_at.isoformat(),
            "metadata": metadata,
        })

        # gap before the next skill in this spec (review/thinking time)
        t = completed_at + timedelta(minutes=random.randint(5, 60))

    return len(skills_this_run) * 2


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=20)
    args = parser.parse_args()

    random.seed(42)  # reproducible
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=WINDOW_DAYS)

    total_events = 0
    for i in range(args.count):
        # Random start time within the window, specs in roughly
        # chronological order for readability but not strictly required.
        offset = random.uniform(0, WINDOW_DAYS * 24 * 60 - 180)  # leave room for the run itself
        base_time = window_start + timedelta(minutes=offset)
        n = run_spec(i, base_time)
        total_events += n
        print(f"  spec {i+1}/{args.count}: {n} events, starting {base_time.strftime('%Y-%m-%d %H:%M')}")

    print(f"\nSeeded {args.count} specs, {total_events} events, spread over the last {WINDOW_DAYS} days.")
    print("Open http://localhost:3000 (Grafana) or http://localhost:8000/dashboard")
    print("Cleanup: docker exec sdd-analytics-postgres-1 psql -U telemetry -d telemetry "
          "-c \"DELETE FROM events WHERE repository='example-repo';\"")


if __name__ == "__main__":
    main()
