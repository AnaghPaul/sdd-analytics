#!/usr/bin/env python3
"""Seed realistic showcase data for a live demo.

Replaces the old bulk demo data (`example-repo` / `DEMO-xxx`) entirely,
except the one real ticket, BC-822 (`mvnx-support-and-engagement-specs`),
which is preserved — and restored from scripts/fixtures/bc822_real_events.json
if the database is ever wiped and re-seeded from scratch.

24 new synthetic tickets across 4 repos:
  stream-1: 5, stream-2: 9, stream-3: 5, mvnx-support-and-engagement-specs: 5
(plus the 1 real BC-822 -> 6 tickets total for that repo once seeded)

~half of each repo's tickets reach all four speckit phases; the rest stop
partway through. Some tickets re-invoke a phase (e.g. plan run twice).
Every skill call within one ticket's chain is separated by a real gap
(never back-to-back) representing review/read time. MCP usage only ever
attaches to specify/plan. Modes are scattered, weighted toward
default/auto with plan as a minority.

Usage: python3 scripts/seed_showcase_data.py
"""
import json
import random
import subprocess
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

TELEMETRY_URL = "http://localhost:8000/api/v1/events"
POSTGRES_CONTAINER = "sdd-analytics-postgres-1"
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "bc822_real_events.json"
WINDOW_DAYS = 10

SKILLS = ["specify", "plan", "tasks", "implement"]
# (min_seconds, max_seconds, min_tokens, max_tokens, min_files, max_files)
RANGES = {
    "specify":   (20, 90, 3000, 12000, 1, 5),
    "plan":      (60, 400, 5000, 30000, 2, 8),
    "tasks":     (30, 150, 3000, 15000, 1, 3),
    "implement": (200, 900, 15000, 60000, 3, 20),
}

REPO_TICKETS = {
    "stream-1": 5,
    "stream-2": 9,
    "stream-3": 5,
    "mvnx-support-and-engagement-specs": 5,
}
# How many of each repo's tickets reach all four phases; the rest stop partway.
REPO_COMPLETE = {
    "stream-1": 3,
    "stream-2": 5,
    "stream-3": 2,
    "mvnx-support-and-engagement-specs": 2,
}

SUMMARIES = {
    "stream-1": [
        "Add proration handling to the mid-cycle plan upgrade flow ({ticket}). Currently PlanUpgradeService.calculateProration() rounds to whole days, causing a known $0.03-$4 discrepancy vs the billing engine's own calculation for partial-month upgrades. Also need to surface the prorated amount in the upgrade confirmation modal before charging.",
        "Fix duplicate invoice line items when a subscription is upgraded and downgraded within the same billing cycle ({ticket}). InvoiceLineItemBuilder doesn't dedupe by planId+cycleStart, so customers who change plans twice see two charges for the same period on their statement.",
        "Add a retry queue for failed entitlement sync webhooks ({ticket}). Currently a failed POST to /internal/entitlements/sync is dropped silently — no retry, no dead-letter queue — meaning a transient downstream outage can leave a customer's entitlements permanently stale until manually resynced.",
        "Expose a self-serve 'download past invoices' CSV export on the billing history page ({ticket}). Product wants parity with the existing PDF-per-invoice download, but as a single combined CSV covering a selected date range.",
        "Migrate the legacy discount_code free-text field to a normalized Promotion entity with start/end dates and stacking rules ({ticket}). Current implementation just does a string match against a hardcoded list in ApplyDiscountService, which breaks silently for expired codes.",
    ],
    "stream-2": [
        "Add multi-language support to the FAQ widget's search-as-you-type suggestions ({ticket}). Currently FaqSearchBox.tsx only indexes the English corpus; need to route to the correct per-locale Elasticsearch index based on the tenant's configured language.",
        "Fix the chatbot handoff-to-agent flow losing conversation context ({ticket}). When TobiChatWidget escalates to a live agent, the transcript passed via handoffPayload is missing the last 2-3 user turns due to a race between the websocket close and the handoff POST.",
        "Add a 'was this helpful' thumbs up/down on FAQ answers, feeding into article ranking ({ticket}). Needs a new FaqFeedback table plus a nightly aggregation job that recomputes each article's helpfulness score.",
        "Reduce chatbot cold-start latency on first message ({ticket}). TobiChatWidget currently waits for a full NLU model warm-up round-trip before showing the typing indicator, adding ~1.8s of dead time on first load per session.",
        "Add configurable business-hours-aware auto-responses to the chatbot ({ticket}). Outside business hours, Tobi should surface a different fallback message with an estimated response time, configurable per tenant.",
        "Fix duplicate FAQ articles appearing in search results when a tenant has both a global and tenant-specific override for the same topic ({ticket}). FaqSearchService should prefer the tenant-specific version and suppress the global one, not show both.",
        "Add a bulk-import CSV flow for FAQ articles in the admin console ({ticket}). Support currently has to create articles one at a time through the UI; large tenants with 100+ entries need a bulk path.",
        "Instrument chatbot conversation abandonment (user closes the widget mid-conversation) as its own analytics event ({ticket}). Currently only completed and escalated conversations are tracked — abandonment is invisible.",
        "Fix chatbot widget rendering behind other elements on tenant pages with a high z-index modal open ({ticket}). TobiWidgetLoader's default z-index of 999 loses to several known tenant CSS frameworks.",
    ],
    "stream-3": [
        "Add a per-tenant quiet-hours setting for push notifications ({ticket}). Currently NotificationDispatcher fires immediately regardless of the tenant's local time; need to queue and defer non-urgent notification types until quiet hours end.",
        "Fix email notification digest sending duplicate entries when a user has multiple active sessions ({ticket}). DigestBuilder dedupes by userId but not by (userId, deviceId), so multi-device users get near-duplicate digests.",
        "Add a notification preference center allowing per-category opt-out (billing, product updates, security) ({ticket}). Currently it's all-or-nothing at the account level.",
        "Fix delayed SMS delivery for the incident-alert notification category ({ticket}). These are meant to bypass the normal batching queue but are currently going through the same 5-minute batch window as marketing SMS.",
        "Add delivery-status tracking (sent/delivered/failed) surfaced in the admin notification log ({ticket}). Currently once a notification is dispatched to the provider, there's no visibility into whether it actually landed.",
    ],
    "mvnx-support-and-engagement-specs": [
        "Feature: Extend the TOBi chatbot config panel to support per-locale greeting messages ({ticket}), in ibiza-ui repo. Building on the existing ChatbotConfigPanel (apps/content-config-app/src/components/chatbot/) — needs a localized greetings sub-form and backend support for per-locale overrides.",
        "Feature: Add a config-panel audit log showing who changed chatbot settings and when ({ticket}), in ibiza-ui repo. useChatbotConfig currently persists changes with no history — support needs to see the last N changes with actor and timestamp when investigating a misconfiguration report.",
        "Feature: Add a 'preview as tenant' mode to ChatPreviewWidget so support agents can see exactly what a specific tenant's live chatbot looks like, including their overrides ({ticket}), in ibiza-ui repo.",
        "Feature: Add validation to ConfigScriptUrlList to reject non-HTTPS script URLs and known-malicious domains ({ticket}), in ibiza-ui repo. Currently any URL string is accepted and injected as-is.",
        "Feature: Add a disable-with-reason flow to DisableChatbotModal, logging why a tenant's chatbot was disabled for later reporting ({ticket}), in ibiza-ui repo. Currently disabling just flips a boolean with no context captured.",
    ],
}


def post_event(payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(TELEMETRY_URL, data=data, headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req)


def run_psql(sql, capture=False):
    cmd = ["docker", "exec"] + (["-i"] if not capture else []) + [POSTGRES_CONTAINER, "psql", "-U", "telemetry", "-d", "telemetry"]
    if capture:
        cmd += ["-t", "-A"]
    cmd += ["-c", sql]
    return subprocess.run(cmd, check=True, capture_output=capture, text=True)


def clear_old_demo_data():
    """Delete everything except the real BC-822 events."""
    sql = "DELETE FROM events WHERE NOT (repository='mvnx-support-and-engagement-specs' AND ticket_id='BC-822');"
    run_psql(sql)
    print("Cleared old demo data (kept real BC-822).")


def restore_bc822_if_missing():
    result = run_psql(
        "SELECT count(*) FROM events WHERE repository='mvnx-support-and-engagement-specs' AND ticket_id='BC-822';",
        capture=True,
    )
    if int(result.stdout.strip()) > 0:
        print("BC-822 already present, skipping restore.")
        return
    events = json.loads(FIXTURE_PATH.read_text())
    for e in events:
        post_event({
            "event_type": e["event_type"],
            "correlation_id": e["correlation_id"],
            "skill_name": e["skill_name"],
            "repository": e["repository"],
            "branch": e["branch"],
            "ticket_id": e["ticket_id"],
            "timestamp": e["event_timestamp"],
            "metadata": e["metadata"],
        })
    print(f"Restored {len(events)} real BC-822 events from fixture.")


def make_mode_usage(total_tokens):
    """Scattered, weighted toward default/auto; plan as a minority."""
    if random.random() < 0.85:
        primary = random.choice(["default", "auto"])
        if random.random() < 0.3:
            secondary = "auto" if primary == "default" else "default"
            secondary_share = random.uniform(0.2, 0.5)
            secondary_tokens = int(total_tokens * secondary_share)
            return [
                {"mode": primary, "turns": random.randint(5, 30), "tokens": total_tokens - secondary_tokens},
                {"mode": secondary, "turns": random.randint(2, 15), "tokens": secondary_tokens},
            ]
        return [{"mode": primary, "turns": random.randint(5, 40), "tokens": total_tokens}]
    plan_share = random.uniform(0.15, 0.4)
    plan_tokens = int(total_tokens * plan_share)
    base_mode = random.choice(["default", "auto"])
    return [
        {"mode": base_mode, "turns": random.randint(5, 30), "tokens": total_tokens - plan_tokens},
        {"mode": "plan", "turns": random.randint(1, 5), "tokens": plan_tokens},
    ]


def make_mcp_usage(tokens_used):
    """Only ever attached to specify/plan completions by the caller."""
    if random.random() < 0.5:
        return None
    servers = random.sample(["figma", "atlassian"], k=random.choice([1, 1, 2]))
    per = tokens_used // (len(servers) * 4)
    return [{"server": s, "calls": random.randint(1, 6), "tokens": per * (i + 1)} for i, s in enumerate(servers)]


def gap(lo_minutes, hi_minutes):
    return timedelta(minutes=random.randint(lo_minutes, hi_minutes))


def run_ticket(ticket_id, repository, branch, summary, base_time, reach_all_four):
    corr_id = str(uuid.uuid4())

    stop_at = 4 if reach_all_four else random.choices([1, 2, 3], weights=[25, 40, 35])[0]
    skills_this_run = list(SKILLS[:stop_at])

    # Realistic re-run: repeat one already-reached phase once more, right
    # after its first occurrence (e.g. plan re-run after review feedback).
    if skills_this_run and random.random() < 0.4:
        rerun_skill = random.choice(skills_this_run)
        idx = skills_this_run.index(rerun_skill)
        skills_this_run.insert(idx + 1, rerun_skill)

    t = base_time
    for i, skill in enumerate(skills_this_run):
        lo_s, hi_s, lo_t, hi_t, lo_f, hi_f = RANGES[skill]
        duration = random.randint(lo_s, hi_s)
        tokens = random.randint(lo_t, hi_t)
        files = random.randint(lo_f, hi_f)

        post_event({
            "event_type": f"speckit_{skill}_started",
            "correlation_id": corr_id,
            "skill_name": f"speckit-{skill}",
            "repository": repository,
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
        if skill in ("specify", "plan"):
            mcp = make_mcp_usage(tokens)
            if mcp:
                metadata["mcp_usage"] = mcp

        post_event({
            "event_type": f"speckit_{skill}_completed",
            "correlation_id": corr_id,
            "skill_name": f"speckit-{skill}",
            "repository": repository,
            "branch": branch,
            "ticket_id": ticket_id,
            "timestamp": completed_at.isoformat(),
            "metadata": metadata,
        })

        # Never back-to-back. A same-skill rerun gets a shorter "quick
        # iteration" gap; moving to a new phase gets a longer "read and
        # digest the spec/plan/tasks" gap.
        is_rerun_next = i + 1 < len(skills_this_run) and skills_this_run[i + 1] == skill
        t = completed_at + (gap(20, 150) if is_rerun_next else gap(45, 480))

    return len(skills_this_run) * 2


def main():
    random.seed(7)
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=WINDOW_DAYS)

    clear_old_demo_data()
    restore_bc822_if_missing()

    total_events = 0
    ticket_num = 901
    for repository, count in REPO_TICKETS.items():
        n_complete = REPO_COMPLETE[repository]
        complete_flags = [True] * n_complete + [False] * (count - n_complete)
        random.shuffle(complete_flags)
        for i in range(count):
            ticket_id = f"BC-{ticket_num}"
            ticket_num += 1
            reach_all_four = complete_flags[i]
            summary = random.choice(SUMMARIES[repository]).format(ticket=ticket_id)
            branch = f"feature/{ticket_id}"
            # Leave a day of runway before "now" for the multi-hour lifecycle + gaps.
            offset_minutes = random.uniform(0, WINDOW_DAYS * 24 * 60 - 24 * 60)
            base_time = window_start + timedelta(minutes=offset_minutes)
            n = run_ticket(ticket_id, repository, branch, summary, base_time, reach_all_four)
            total_events += n
            status = "complete" if reach_all_four else "partial"
            print(f"  {ticket_id} ({repository}): {n} events, {status}, starting {base_time.strftime('%Y-%m-%d %H:%M')}")

    print(f"\nSeeded {ticket_num - 901} tickets, {total_events} synthetic events, "
          f"spread over the last {WINDOW_DAYS} days, plus the preserved real BC-822.")
    print("Open http://localhost:3000 (Grafana) or http://localhost:8000/dashboard")


if __name__ == "__main__":
    main()
