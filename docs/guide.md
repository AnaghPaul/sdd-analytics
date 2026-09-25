# Guide

Everything beyond the quickstart in the main [`README.md`](../README.md):
how it works, the event schema, the Grafana dashboard in detail,
seeding demo data, instrumenting your own repo, troubleshooting, and
current known limitations.

## How it works

```
Developer runs /speckit-specify in an instrumented repo
                    │
                    ▼
     Claude Code hook fires (PostToolUse / Stop)
                    │
                    ▼
     hooks/telemetry_hook.py
       - figures out which skill ran, which feature/ticket it's for
       - fire-and-forget POST to the telemetry API
                    │
                    ▼
          FastAPI server (server/app/main.py)
                    │
                    ▼
              PostgreSQL (events table)
                    │
                    ▼
              Grafana dashboard
```

Two separate things live in this repo:

1. **The telemetry API + database** (`docker-compose.yml`, `db/`,
   `server/`) — a service you run once, locally, and leave running.
2. **The hook script** (`hooks/telemetry_hook.py`) — copied into
   *other* repos (the ones actually using Spec-kit) and wired up via
   their `.claude/settings.json`. It's what detects skill usage and
   sends events here. See
   [Instrumenting another repo](#instrumenting-another-repo) below.

## Event schema

Every row in `events` has these top-level columns:

| Column | Notes |
|---|---|
| `id` | `UUID`, server-generated (`gen_random_uuid()`) — not an auto-increment integer |
| `event_type` | e.g. `speckit_specify_started`, `speckit_plan_completed` |
| `correlation_id` | One per spec — links `specify → plan → tasks → implement` together |
| `skill_name` | `speckit-specify` / `speckit-plan` / `speckit-tasks` / `speckit-implement` |
| `repository`, `branch` | |
| `ticket_id` | Best-effort — see [Current scope](#current-scope-deliberately-limited) |
| `event_timestamp`, `received_at` | Client-reported vs. server-insert time |
| `metadata` | JSONB — everything else, so new fields never need a migration |

`metadata` currently carries:

| Field | On which events | Source |
|---|---|---|
| `summary` | started + completed | Raw text typed after the slash command |
| `duration_seconds` | completed only | `completed_at − started_at` |
| `agent_name` | started + completed | Always `"claude"` |
| `agent_version` | started + completed | The model in use (e.g. `claude-sonnet-5`), read from the session transcript |
| `skill_version` | started + completed | Spec-kit's own install version, from `.specify/init-options.json` |
| `tokens_used` | completed only | Summed `input_tokens + output_tokens` from the transcript, within the skill's time window |
| `files_considered` | completed only | Count of distinct files touched (`Read`/`Edit`/`Write`) in that window |
| `mcp_usage` | completed only | Array of `{server, calls, tokens}` — one entry per MCP server touched (e.g. `figma`, `atlassian`), parsed from `mcp__<server>__<tool>` tool-call names in the transcript |
| `mode_usage` | completed only | Array of `{mode, turns, tokens}` — one entry per Claude Code permission mode used (`default`/`auto`/`plan`) |

Not implemented: `ticket.type` (would need a live Jira lookup —
deliberately out of the fire-and-forget hook path, see
[`docs/decisions.md`](decisions.md) #11 and #21).

## Repo layout

```
core-idea.md          original notes on the idea
docs/decisions.md      running log of design decisions and why
docker-compose.yml     Postgres + Grafana container definitions
db/init.sql            creates the events table
server/                the FastAPI telemetry API
  requirements.txt
  app/
    main.py            POST /api/v1/events, GET /api/v1/events,
                        GET /api/v1/stats, GET /api/v1/specs,
                        GET /api/v1/mcp-stats, GET /api/v1/mode-stats,
                        GET /dashboard, GET /health
    db.py              Postgres connection pool
    static/
      dashboard.html   the plain-HTML dashboard (vanilla JS, no build step)
hooks/
  telemetry_hook.py    the hook script (source copy — gets deployed
                        into instrumented repos' .claude/hooks/)
grafana/provisioning/   dashboard-as-code: datasource + dashboard JSON,
                        auto-loaded and auto-reloaded by Grafana
scripts/                demo-data seed scripts (see below)
```

## Grafana dashboard

`docker-compose up -d` (or `./start.sh`) also starts Grafana on
**http://localhost:3000**. Default login is `admin`/`admin` on a fresh
volume — change it if this ever runs anywhere other than your own
machine. Both the Postgres datasource and the dashboards are
provisioned automatically from files in `grafana/provisioning/` —
nothing to click through manually.

Two dashboards are provisioned:

- **Spec-kit Telemetry** (the main one) — 6 stat tiles (total events,
  completed runs, total active skill time, avg duration/run, total
  tokens, avg wall-clock time per ticket), an events-by-skill bar
  chart, duration/token trend panels, a time-used-by-ticket bar chart,
  MCP server usage, permission-mode usage, a Specs table, and a
  Recent Events table.
- **Event Detail** (in its own "Internal — Drill-down Views" folder,
  not meant to be opened directly) — click **any row's "Event" cell**
  in Recent Events to jump here and see every field of that one event,
  including `metadata`, on its own full-width page.

**Drill-down filters**, chained in a strict one-directional hierarchy
so you can never land on an impossible/empty combination as long as
you pick top-to-bottom: `repository → ticket → correlation_id →
{skill, mcp_server, mode}`. "All" is the default and means no filter
on that dimension.

Filter queries use a `('$var' = '__ALL__' OR ...)` SQL pattern rather
than Grafana's more common multi-value `IN ($var)` convention (`IN`
breaks for the MCP/mode filters specifically — see
[`docs/decisions.md`](decisions.md) #31), and reference variables via
`${var:raw}` rather than bare `$var` to avoid Grafana's inconsistent
default auto-quoting between real query values and a custom `allValue`
sentinel (see #35 if present, or ask — this was a real bug found via
a live browser test, not theory).

If you edit a dashboard JSON file by hand, it's picked up automatically
within ~10 seconds (no restart needed) — the file provider re-scans on
that interval. To pull a UI-made edit back into the file:
```bash
curl -s -u admin:<password> http://localhost:3000/api/dashboards/uid/spec-kit-telemetry \
  | python3 -c "import json,sys; d=json.load(sys.stdin)['dashboard']; d.pop('id',None); d.pop('version',None); print(json.dumps(d, indent=2))"
```

## Seeing it with demo data

You don't need an instrumented Spec-kit repo to see the dashboards
populated. Three seed scripts, in increasing order of realism:

- **`python3 scripts/seed_demo_data.py`** — 3 specs in different
  states (one fully done with MCP usage and some plan-mode turns, one
  in progress with a re-run `plan` step, one with no ticket), 24
  events, spread over a backdated 3-hour timeline.
- **`python3 scripts/seed_bulk_demo_data.py --count 20`** — 20 specs,
  ~120 events, randomized-but-plausible durations/tokens, spread over
  the last 14 days — makes the Grafana trend panels look like actual
  trends instead of 2-3 sparse points.
- **`python3 scripts/seed_showcase_data.py`** — the most realistic
  option: 24 tickets across 4 repos, real-sounding multi-sentence
  summaries, some tickets re-running a phase, deliberate non-zero gaps
  between phases (never back-to-back), MCP usage only on
  `specify`/`plan`, spread over the last 10 days. Also preserves/
  restores one genuinely real event chain from actual usage
  (`scripts/fixtures/bc822_real_events.json`) and clears prior demo
  data first (everything except that one real ticket).

Clean up demo data:
```bash
docker exec sdd-analytics-postgres-1 psql -U telemetry -d telemetry \
  -c "DELETE FROM events WHERE repository IN ('example-repo','stream-1','stream-2','stream-3');"
```
(To wipe *all* data and start completely fresh:
`docker exec sdd-analytics-postgres-1 psql -U telemetry -d telemetry -c "TRUNCATE TABLE events RESTART IDENTITY;"`)

## Trying it with your own real Spec-kit repo

Once you've [instrumented your repo](#instrumenting-another-repo),
open a Claude Code session there and run a skill:

```bash
cd <path-to-your-instrumented-repo>
claude
```

```
/speckit-specify Add a button that lets support agents reset a customer's password
```

While it runs, you can peek at the in-flight state (this file appears
while a skill is running and disappears when it finishes):

```bash
cat <path-to-your-instrumented-repo>/.claude/.telemetry-state.json
```

Then open **http://localhost:3000** (Grafana) or
**http://localhost:8000/dashboard** (plain HTML) to watch events land.
Both auto-refresh, so you can leave one open while you work.

Or query the raw data directly:
```bash
docker exec sdd-analytics-postgres-1 psql -U telemetry -d telemetry \
  -c "SELECT id, event_type, correlation_id, skill_name, repository, branch, metadata FROM events ORDER BY id;"
```

## If nothing shows up

The hook is fire-and-forget and silent by design (it must never block
or interrupt a developer's actual work), so a failure won't show up as
an error in the chat. Check:

- Is the API server still running? (`curl http://localhost:8000/health`)
- Test the hook script directly, bypassing Claude Code entirely, to
  isolate whether the problem is the hook or something upstream:

```bash
cd <path-to-your-instrumented-repo>
echo '{"hook_event_name":"PostToolUse","cwd":"'$(pwd)'","tool_name":"Skill","tool_input":{"skill":"speckit-specify"}}' \
  | python3 .claude/hooks/telemetry_hook.py
```

If this produces a row in the database but a real skill invocation
doesn't, the hook's assumption about how Claude Code names the skill
in its internal tool call is wrong for that Claude Code version — flag
it so it can be fixed.

## Instrumenting another repo

To add telemetry to a different Spec-kit repo:

1. Copy `hooks/telemetry_hook.py` into `<repo>/.claude/hooks/telemetry_hook.py`.
2. Add a `.claude/settings.json` in that repo:
   ```json
   {
     "hooks": {
       "PostToolUse": [{"matcher": "Skill", "hooks": [{"type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/telemetry_hook.py\""}]}],
       "Stop": [{"hooks": [{"type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/telemetry_hook.py\""}]}]
     }
   }
   ```
3. Make sure that repo's `.gitignore` excludes
   `.claude/.telemetry-state.json` (the hook's temporary runtime state
   file).

This manual copy step is a known gap — see
[`docs/decisions.md`](decisions.md) for why it hasn't been automated
yet. By default the hook posts to `http://localhost:8000/api/v1/events`
— override with the `TELEMETRY_API_URL` environment variable if your
telemetry API runs somewhere other than `localhost` for you.

## Current scope (deliberately limited)

This is a first iteration. Known gaps, tracked in
[`docs/decisions.md`](decisions.md):

- Only `/speckit-specify`, `/speckit-plan`, `/speckit-tasks`, and
  `/speckit-implement` are instrumented — not `/speckit-constitution`,
  `/speckit-clarify`, or `/speckit-analyze`.
- Failed telemetry POSTs are silently dropped, not retried.
- No auth on the API — it's designed to run locally, one instance per
  person, not as a shared multi-user server.
- The hook script is manually copied per repo, not distributed as a
  plugin.
- Doesn't yet align with the Confluence "AI-PDLC Trial" brief's
  measurement requirements (active-effort tracking, approval gates,
  quality scorecard) — deliberately deferred to get one working
  iteration first.
- `ticket_id` is best-effort (regex on the `/speckit-specify` prompt,
  no Jira lookup); `ticket.type` isn't captured at all for the same
  reason. `tokens_used` doesn't include cache tokens.
- The plain HTML dashboard is vanilla JS, not React — styled with
  plain hand-rolled CSS (see [`docs/decisions.md`](decisions.md) #18).
- "Completed" is inferred, not directly observed: it's based on the
  next thing that happens after a skill starts (either `Stop` firing,
  or the *next* skill starting, which implicitly means the previous
  one finished). One accepted-risk case remains: if a skill ever
  pauses mid-run to ask a question, it could still log a too-early
  completion. See [`docs/edge-cases.md`](edge-cases.md) #1 and #9.
- No dedicated "activity over time" / adoption-trend panel today —
  see [`docs/decisions.md`](decisions.md) for why that slot was
  repurposed.
