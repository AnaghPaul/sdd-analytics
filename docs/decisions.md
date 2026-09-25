# Decisions Log

Running log of decisions made while designing Spec-kit telemetry. Each
entry captures what was decided and why, so the reasoning survives even
if the conversation that produced it doesn't. Superseded decisions are
marked rather than deleted.

## 1. Hook distribution: centralized, not per-repo

**Decision:** The hook implementation is a single shared artifact
(script + config) pushed into every Spec-kit-enabled repo's
project-level `.claude/settings.json` — not copied and maintained
independently per repo.

**Why:** A dashboard aggregating across teams needs homogeneous event
data. Per-repo copies would drift in schema/behavior over time.

## 2. Definition of "done" (deferred)

**Decision:** For now, "done" is not tied to a Jira status
transition or PR merge. We're treating dev-side completion (last
in-scope skill finishing) as good enough for the POC. Revisit once
the core pipeline works.

**Why:** PRs don't map 1:1 to tickets (fan-out, split work, non-Spec-kit
changes), so anchoring to PR state was unreliable. Jira status has
promise (avoids the PR fan-out problem, uses an org-agreed source of
truth) but needs its own design pass — see open item below.

## 3. POC deployment target: local

**Decision:** Telemetry API + Postgres run locally (e.g. Docker
Compose) for this phase, not on shared/cloud infra.

**Why:** Fastest path to proving the pipeline works end-to-end before
any infra investment.

## 4. Failure handling: fire-and-forget now, queue+retry later

**Decision:** If the telemetry API is unreachable when a hook fires,
the hook logs a warning and drops the event — no blocking, no retry,
for this phase. A local queue with retry is planned as a later
improvement, not part of the current design.

**Why:** Telemetry must never slow down or break a developer's actual
workflow. Reliability can be layered in once the pipeline is proven.

## 5. Skill scope: core four

**Decision:** Only `/specify`, `/plan`, `/tasks`, and `/implement` are
instrumented initially. `/constitution`, `/clarify`, `/analyze` are
out of scope for now.

**Why:** These four cover the primary lifecycle questions (duration,
iteration count, skill usage) without instrumenting every command
up front.

## 6. Ticket ID resolution: Jira MCP primary, branch-name fallback

**Decision:** The hook resolves ticket ID by querying Jira first
(same auth path as MCP/Jira REST), falling back to parsing the git
branch name (e.g. `feature/ABC-123`) if that lookup fails or times out.

**Why:** Jira is the authoritative source; branch parsing is a cheap,
offline-friendly fallback so ticket ID resolution doesn't hard-depend
on Jira being reachable.

## 7. Hook mechanism: Claude Code hooks, not prompt-instructed POSTs

**Decision:** Telemetry is emitted via Claude Code hooks
(`PostToolUse` matched on the `Skill` tool, filtered in-script to the
four in-scope skills, plus a completion signal — see open item below)
rather than instructing each skill's prompt to call the telemetry API
itself.

**Why:** Hooks fire deterministically regardless of what the model
does; prompt-instructed POSTs depend on the model faithfully executing
them every time, which is a weaker guarantee for data meant to be
ground truth. Hook config must live in **project-level**
`.claude/settings.json` (per opted-in repo), not the developer's
global config, so it doesn't fire in unrelated projects.

## 8. Server stack: FastAPI + Postgres

**Decision:** The telemetry API is built with FastAPI, backed by a
single Postgres table with a JSONB metadata column.

**Why:** Fastest to stand up for this POC; Python also suits the Jira
REST lookup, and if hook scripts are Python too, payload-building and
ticket-resolution logic can be shared instead of duplicated.

## 9. Completion detection: state file + bare Stop, no artifact check

**Decision:** `PostToolUse(Skill)` (filtered to the four in-scope
skills) writes a local state file `{skill, ticket_id, started_at}` and
POSTs `*_started`. The next `Stop` hook, if a state file exists, POSTs
`*_completed` (duration = now − started_at) and deletes the state
file — no verification that an artifact file was actually written.

**Why:** `Stop` only fires once per conversational turn (when the
assistant finishes and yields control, not after each internal tool
call), so it only risks firing early if a skill pauses mid-run to ask
the developer a question. Spec-kit already isolates clarification into
its own skill (`/clarify`, out of scope), so `/specify`, `/plan`,
`/tasks`, `/implement` are expected to run straight through. Accepted
residual risk: if a skill unexpectedly pauses, we log a too-early
completion and miss the real one. Not solving for that now — same
fire-and-forget spirit as decision #4.

## 10. "First artifact write" metric: deferred, not v1

**Decision:** Not adding a separate hook to detect the first write of
`spec.md`/`plan.md`/`tasks.md` (a tighter "drafting speed" signal,
distinct from the `Stop`-based `*_completed` duration). Noted as a
future addition once the core start/completed pipeline is proven.

**Why:** Marginal value for a third hook filter before the basic
pipeline even works. YAGNI for the POC.

## 11. Correlation ID: Spec-kit's own `feature_directory`, not ticket ID

**Decision:** `correlation_id` = the `feature_directory` value from
`.specify/feature.json` (e.g. `009-tenant-table-neo-design`), not the
Jira ticket ID. `ticket_id` becomes an optional, best-effort enrichment
field (nullable), not the join key. **Supersedes decision #6** —
dropping the Jira-MCP-primary / branch-parse-fallback resolution
entirely for v1.

**Why:** Checked the real target repo
(`mvnx-support-and-engagement-specs`). Branch names don't carry ticket
IDs in any parseable form (`faq-admin-page`, `poc-faq-tobi`) — the
assumed `feature/ABC-123` convention doesn't hold here. Ticket keys
only show up inconsistently in commit message brackets (e.g.
`[BC-633]`), which isn't available at hook-fire time anyway. But
`.specify/feature.json` already tracks the active feature directory
as Spec-kit's own state — always present the instant any speckit skill
runs, no network call, no ambiguity. This also removes the Jira
network call from the hook's fire-and-forget path, which is a win on
its own.

**Note:** actual skill names in this repo are `speckit-specify`,
`speckit-plan`, `speckit-tasks`, `speckit-implement` (prefixed), not
bare `specify`/`plan`/etc. Matcher/filter logic needs the prefix.

## 12. Confluence "AI-PDLC Trial" brief: not aligning yet

**Decision:** Deliberately not adopting the AI-PDLC trial brief's
12-stage flow, scorecard, or scope (constitution/clarify reinstatement,
active-effort tracking, approval-gate events) for this iteration. Continuing
with the design as already agreed before the brief surfaced.

**Why:** The brief is new, work here started before it existed, and
chasing full alignment now would stall a first working iteration. Get
one end-to-end iteration (hook → API → DB) working first; reconcile
with the brief's requirements afterward.

## 13. Correlation ID for `specify`: minted UUID, coupled to the spec via a marker file

**Decision:** `speckit-specify` no longer derives `correlation_id` from
`.specify/feature.json` at all. On start it mints a fresh UUID; on stop
(once the skill has actually finished and the spec folder exists) it
writes that UUID into `specs/NNN-xxx/.telemetry.json`
(`{"correlation_id": "<uuid>"}`) inside the spec it just produced. This
marker file **is committed to the repo**, not gitignored — unlike
`.claude/.telemetry-state.json`, which stays scratch/local.
`speckit-plan`/`speckit-tasks`/`speckit-implement` resolve
`correlation_id` by reading this marker from the current feature
directory (still safe to read `feature.json` live for these three,
since they don't change which feature is current). If the marker is
missing (specs created before this fix), they fall back to the folder
name and write the marker themselves — self-healing, no migration
script needed.

**Why:** Found a real bug (**partially supersedes #11**) —
`PostToolUse(Skill)` fires the instant `speckit-specify` is invoked,
before it has created the new spec folder or updated `feature.json`.
So `feature.json` is guaranteed stale specifically at `specify`-start
time (not for the other three skills, which never change the pointer).
The old design read it anyway, so a `specify` run's `started` and
`completed` events could end up tagged with different
`correlation_id`s — breaking the one join key the whole system exists
to provide.

**Known unresolved edge case:** if `/speckit-specify` is re-run to
update an *existing* spec in place (not create a new one), the fresh
UUID minted at start won't match the spec's already-coupled id, so
that run's own started/completed pair can still end up orphaned from
the spec's real history. Rarer than the create-new-spec path; not
solving now.

## 14. Repo root: `$CLAUDE_PROJECT_DIR`, not `cwd`-derived `git rev-parse`

**Decision:** `repo_context()` now resolves `repo_root` from the
`$CLAUDE_PROJECT_DIR` environment variable (already used to locate the
hook script itself in `settings.json`) when it points at a valid git
repo, falling back to `git rev-parse --show-toplevel` from the hook
payload's `cwd` only if that variable is unset.

**Why:** Found via a real `speckit-implement` run producing zero
events (see `edge-cases.md` #8). `implement` was actively editing
files inside `src/tobi-faq-spike`, a separate git repo nested as a
submodule. Resolving `repo_root` from `cwd` breaks the instant the
session's tracked working directory drifts into a nested repo —
`git rev-parse --show-toplevel` from in there resolves to the
submodule, not the parent project, so `.specify/feature.json` isn't
found and the hook silently no-ops. `$CLAUDE_PROJECT_DIR` is fixed at
session launch and immune to this drift.

## 15. `ticket_id` and `summary`: regex-extracted from the skill's `args`, coupled via the marker file

**Decision:** The `Skill` tool's `tool_input` includes an `args` field —
the raw text typed after the slash command. On every skill start, that
text (truncated to 500 chars) becomes `metadata.summary` for that
event. On `speckit-specify` specifically, it's also regex-scanned for a
Jira-style key (`[A-Z][A-Z0-9]{1,9}-\d+`) to populate `ticket_id`,
which — like `correlation_id` — gets written into the spec's marker
file (`specs/NNN-xxx/.telemetry.json`, now `{correlation_id,
ticket_id}`) so `plan`/`tasks`/`implement` inherit the same ticket_id
without re-deriving it.

**Why:** Closes real gaps from the core-idea.md gap analysis — `metadata`
was always empty besides `duration_seconds` (question 6, "capturing the
prompt," was never actually implemented), and `ticket_id` was a dead
column nothing populated since decision #11 dropped Jira/branch
resolution. No live Jira call needed — this is a local, zero-network
regex against text already available in the hook payload. Tradeoff:
best-effort only — if the ticket isn't mentioned in the `specify`
prompt, `ticket_id` stays empty.

## 16. `tokens_used`: summed from the session transcript, not the hook payload

**Decision:** At `Stop`, the hook reads `payload["transcript_path"]`
(a JSONL file, already provided by Claude Code, previously unused),
sums `usage.input_tokens + usage.output_tokens` across `type:
"assistant"` entries whose `timestamp` falls within
`[started_at, completed_at]`, and adds it to the `*_completed` event's
`metadata.tokens_used`.

**Why:** Verified feasible by inspecting a live transcript file
directly — each assistant turn logs a real `usage` block. This was
listed as "later" in the original `core-idea.md`; turned out cheap
once the transcript path was actually looked at. Doesn't include cache
tokens (input/output only) and is bounded by the transcript's own
timestamp granularity — not solving for anything beyond a reasonable
approximation.

## 17. Dashboard "Specs" view: grouped by `correlation_id`

**Decision:** Added `GET /api/v1/specs` (one row per `correlation_id`:
ticket, summary, skills run, completed/total runs, total duration,
total tokens, last activity) and a corresponding dashboard table.
Clicking a spec row filters the whole dashboard to `?correlation_id=...`,
combinable with the existing `?skill=...` filter.

**Why:** Directly answers the "how many iterations does a spec need"
and "how long does this specific spec take end-to-end" questions from
`core-idea.md` — previously the data existed but there was no view for
it, only per-skill aggregates.

## 18. Dashboard restyled with hand-rolled CSS custom properties

**Decision:** The dashboard's inline `<style>` block now defines a
small set of `:root { --* }` custom properties for color, spacing, and
radius, and every CSS rule in `dashboard.html` references these
variables instead of one-off hardcoded values. Layout widened from a
960px centered column to 1440px with generous padding. Skill colors
come from a small set of named accent variables
(`--accent-{blue,yellow,lime,fuchsia}`) instead of the dataviz-skill
palette used before. Light mode only.

**Why:** Centralizing color/spacing/radius into named custom
properties keeps `dashboard.html` easy to re-theme without touching
every rule, while keeping the "static HTML, zero build step"
architecture from decision #8 intact — no React, bundler, or external
CSS dependency required.

## 19. Dashboard: search + sortable columns

**Decision:** Added a single search box (`/regex/`-or-substring,
case-insensitive) above the Specs/Events tables that filters both
against every field in each row (matches via `JSON.stringify(row)`,
not a fixed field list). Added click-to-sort on Duration and Tokens
columns in both tables. All client-side against already-fetched data
(events fetch limit raised from 25 to 200 to give search something
meaningful to work with) — no new backend endpoints needed.

**Why:** Directly requested; also cheap since the dashboard already
fetches and caches this data for the 5s polling refresh.

## 20. Completion detection: a new skill starting implicitly completes the previous one

**Decision:** `handle_post_tool_use` now checks whether a state file
already exists *before* writing the new skill's state. If one does,
that in-flight skill is finalized right there — its `*_completed`
event is posted (with completion time = now) via a new shared
`finalize_run()` helper — before the new skill's state overwrites it.
`handle_stop` now just loads the state and calls the same
`finalize_run()`.

**Why:** Found via real usage (see `edge-cases.md` #9) — a genuine
`plan → tasks → implement` chain, all invoked within one conversational
turn, silently lost `plan`'s and `tasks`'s completed events entirely.
The single-slot state file assumed one skill always finishes (via
`Stop`) before the next starts; `Stop` only fires once per turn, so
chaining multiple skills in one turn broke that assumption completely
— each new start clobbered the previous skill's in-flight state before
its `Stop` ever fired. This is the inverse failure mode of the
accepted risk in decision #9 (`Stop` firing too *early*) — this is
`Stop` firing too *late*, after several skills already got silently
dropped. Two real orphaned events from this were manually backfilled
using the session transcript for real duration/token numbers, flagged
`"backfilled": true`.

## 21. Filled out the remaining `core-idea.md` fields: agent, skill version, files considered

**Decision:** Added `metadata.agent_name` (always `"claude"`),
`metadata.agent_version` (the model in use, e.g. `claude-sonnet-5` —
read from the session transcript, same source as `tokens_used`),
`metadata.skill_version` (Spec-kit's own install version, from
`.specify/init-options.json`), and `metadata.files_considered` (count
of distinct files touched via `Read`/`Edit`/`Write` in the skill's time
window, from the same transcript pass as `tokens_used`). All four are
populated on `started` where knowable at that point (agent/skill
version), and all four plus duration/tokens on `completed`.
`ticket.type` from the original model is deliberately *not* added —
see below.

**Why:** Closes the remaining gap identified when reconciling against
`core-idea.md`'s original JSON example (agent/skill/context were the
only pieces never wired up). No schema migration needed for any of
this — `metadata` is JSONB precisely so this kind of addition is free.
Verified each field's data source was real and available (transcript
`model` field, `Read`/`Edit`/`Write` tool_use blocks) before
implementing, same as `tokens_used` — see decision #16.

**`ticket.type` — deliberately not added:** it would require a live
Jira lookup, which decision #11 explicitly removed from the hook's
fire-and-forget path for reliability reasons. Adding a field that's
permanently `null` with no path to populate it would be clutter, not
data — better to leave it undocumented as a schema field and just note
the gap here than pretend it's tracked.

## Open items (not yet decided)

- **Iteration counting and outcome/status fields** — not part of
  `core-idea.md`'s original event shape but flagged early on as useful
  (see the very first conversation about this idea); still not a
  first-class field, only derivable by counting `*_started` events per
  `(correlation_id, skill_name)`.
- **`ticket.type`** — needs a live Jira lookup; deliberately out of
  scope, see decision #21.
- **"Done" definition** (see #2) — Jira status category as a future
  finish line, once the core pipeline is proven.
- **Confluence brief reconciliation** (see #12) — active-effort
  tracking, approval-gate events, constitution/clarify coverage,
  quality scorecard — revisit after the first iteration.
- **Team mapping** (`repo_teams` lookup table) — discussed early on,
  never actually built. `repository` is captured; there's no team join.
- **`ticket_id` extraction approach** — current regex-on-`args` method
  (decision #15) works but is best-effort; user flagged wanting to
  discuss a better approach, deferred for later.

## 22. Event detail modal, generic over `metadata`

**Decision:** Clicking an events-table row opens a modal (styled with
the dashboard's own CSS variables: card surface, border-radius,
spacing) showing every field for
that event — the known columns plus every key in `metadata`, rendered
by iterating `Object.entries(metadata)` rather than a hardcoded field
list. Dropped `Summary` as a table column entirely (kept in the modal
only); the correlation-ID cell's existing "filter to this spec" link
still works via `stopPropagation` so it doesn't also open the modal.

**Why:** Directly requested — long fields like `summary` don't belong
in a table cell, and after decision #21 there were now fields
(`agent_version`, `skill_version`, `files_considered`) with nowhere to
surface at all. Iterating `metadata` generically means the next field
added there shows up in the modal automatically, no dashboard code
change required — same JSONB philosophy as the rest of this system.

## 23. Primary key: UUID, not `BIGSERIAL`

**Decision:** `events.id` is now `UUID PRIMARY KEY DEFAULT
gen_random_uuid()`, not an auto-incrementing integer. Migrated the
live table in place (`ALTER COLUMN id TYPE UUID USING
gen_random_uuid()`) rather than dropping and recreating it. `gen_random_uuid()`
is native to Postgres 16 — no extension needed. Also fixed a real bug
this surfaced: the dashboard's row-click handler
(`showEventDetail(${e.id})`) assumed a bare integer; a UUID's hyphens
would have parsed as JS subtraction operators. Now quoted:
`showEventDetail('${e.id}')`.

**Why:** Requested directly — an auto-increment integer sitting next
to a UUID `correlation_id` was an inconsistency worth fixing. This is
a genuine schema change unlike decisions #15/#16/#21 — the column
*type* itself changed, not just what goes into JSONB, so `db/init.sql`
needed a real edit this time.

**Incident:** while verifying that `TRUNCATE TABLE events RESTART
IDENTITY` (documented in the README) still behaves correctly against
a UUID column with no backing sequence, ran it against the live
database instead of a disposable one — genuinely deleted all 14 real
events tracked that day (the `010-tobi-chatbot-preview` `specify →
plan → tasks → implement` history). No backup existed. The underlying
spec-kit work (code, git history) was untouched — only the telemetry
*about* it was lost. `RESTART IDENTITY` itself is confirmed harmless
on a UUID column (silently a no-op, doesn't error). Recording this
plainly rather than glossing over it, same as every other mistake in
this log.

## 24. MCP server usage tracking (`mcp_usage`)

**Decision:** `analyze_transcript_window()` now also detects MCP tool
calls in the transcript — any `tool_use` block whose name matches
`mcp__<server>__<tool>` (confirmed real convention: Claude Code names
all MCP tool calls this way). For each such call, records which
server was touched. Token attribution: the *whole turn's* tokens are
attributed to *each distinct server* touched in that turn (not split
between servers sharing a turn, and not double-counted within one
server if it's called multiple times in the same turn) — the closest
honest approximation available, since usage is only ever reported per
turn, never per individual tool call. Result lands in
`metadata.mcp_usage` as `[{server, calls, tokens}, ...]` on completed
events only (same timing as `tokens_used`/`files_considered`, since it
needs the full window).

Added `GET /api/v1/mcp-stats` (global aggregate: calls/tokens/event
count/spec count per server) and an `mcp_server` filter on
`/api/v1/events` and `/api/v1/stats`, mirroring the existing
`skill_name`/`correlation_id` filter pattern. Dashboard: a new "MCP
server usage" table (click a row to filter, same pattern as Specs),
and an MCP badge column on the events table showing which servers a
run touched with calls/tokens on hover.

**Why:** Directly requested — visibility into what Figma/Atlassian MCP
calls actually cost in tokens. Verified the naming convention and real
usage existed *before* building anything: grepped real session
transcripts in the target repo and found genuine `mcp__figma__*` and
`mcp__atlassian__*` calls already there. Verified the shared-turn
token attribution logic with a synthetic transcript exercising that
exact case (one turn touching two servers) before syncing.

## 25. Seed-data script: rewritten in Python, not bash arrays

**Decision:** The README's demo seed script was rewritten from a bash
loop over parallel arrays (`for i in 0 1 2 3; do ... ${arr[$i]} ...`)
to a single Python heredoc.

**Why:** Found a real, previously-unnoticed bug while extending the
seed script to include `mcp_usage` — bash-style `${arr[$i]}` indexing
is 0-based, but **zsh's default is 1-based**. This machine's default
shell is zsh (macOS default), so `${durations[0]}` was silently empty
every single time this script had been run in this session, including
in earlier verification passes. This is exactly what produced the
stray `speckit-`/`speckit__started` row misdiagnosed earlier as
"leftover test debris" — it wasn't debris, it was this bug,
deterministically mangling one of the four seeded runs every time.
Confirmed by reproducing it in isolation (`${durations[$i]}` empty for
`i=0` in zsh, non-empty via `${durations[@]}`). Python has no such
shell-dependent indexing ambiguity, so it was the correct fix, not a
workaround — re-verified the exact script text now in the README,
copy-pasted fresh, under both zsh and explicit bash, producing correct
data both times.

## 26. Fixed `list_events` ordering: `event_timestamp DESC`, not `id DESC`

**Decision:** `GET /api/v1/events` now orders by
`event_timestamp DESC, received_at DESC` instead of `id DESC`.

**Why:** Real regression from decision #23. `id DESC` correctly meant
"most recent first" when `id` was a `BIGSERIAL` (monotonically
increasing with insertion order). Once `id` became a random UUID
(`gen_random_uuid()`), that ordering became meaningless — random UUIDs
don't sort chronologically at all, so the events table could show rows
in an effectively arbitrary order regardless of when they actually
happened. Missed at the time of decision #23; surfaced now while
preparing seed data with deliberately varied timestamps, which would
have been pointless to build without this fix. The Specs endpoint was
never affected — it already ordered by `max(event_timestamp) DESC`.

## 27. Richer demo dataset: `scripts/seed_demo_data.py`

**Decision:** Added a second, richer seed script (separate from the
README's minimal one-spec smoke test) covering every dashboard feature
at once: 3 specs in different states — one fully done touching Figma +
Atlassian MCP, one still in progress with a `plan` re-run (iteration
signal: 4 runs across 3 distinct skills), one with no ticket mentioned
(`ticket_id: null`). 24 events, timestamps backdated across a 3-hour
window with each `*_completed` offset from its `*_started` by the
actual `duration_seconds` (not the same instant), plus a small real
sleep (0.4s) between posts so a dashboard left open updates visibly
during the run.

**Why:** Directly requested — wanted to demo the whole dashboard, and
pointed out that identical timestamps everywhere looked unrealistic.
Verified end to end: correct chronological ordering, correct
iteration signal on Spec B (total_runs=4, 3 distinct skills), correct
null ticket on Spec C, correct MCP aggregation across the two specs
that touched Atlassian (3 calls, 3500 tokens, 2 specs) — all confirmed
via the actual API responses, not assumed from the script's logic.

## 28. Filter clicks: `history.pushState` + re-render, not full navigation

**Decision:** Every filter interaction (bar chart, MCP stats row, Specs
row, correlation-ID link, "Clear ×" chips) now goes through a
`navigate(url)` helper — `history.pushState(null, '', url)` then
`refresh()` — instead of `window.location.href = url` or a bare
`<a href>`. Added a `popstate` listener so the browser back/forward
buttons still work correctly.

**Why:** A real full-page navigation resets scroll to the top every
time, which is jarring when filtering from partway down a long page —
directly reported. `refresh()` already re-fetches and re-renders in
place from the URL's query params, so there was no need for an actual
page load at all; `pushState` gets the same shareable/bookmarkable
URLs without it.

## 29. Permission-mode usage tracking (`mode_usage`)

**Decision:** `analyze_transcript_window()` also tracks Claude Code's
permission mode (`default`/`auto`/`plan`) from `type: "permission-mode"`
transcript entries — these carry no timestamp of their own, so mode is
tracked as running state across the *entire* file in file order (not
scoped to `[start, end]`), otherwise a mode set before the window
started would look unknown. Every assistant turn's tokens are
attributed to whichever mode was current at that point. Unlike
`mcp_usage`, every turn belongs to exactly one mode, so `mode_usage`
tokens summed always equal `tokens_used` exactly — verified against
real data (513328 + 14185 = 527513, the real transcript's total).
Result: `metadata.mode_usage = [{mode, turns, tokens}, ...]` on
completed events. Added `GET /api/v1/mode-stats` and a `mode` filter on
events/stats, mirroring the `mcp_server` pattern exactly. Dashboard: a
"Permission mode usage" table (click to filter). No new events-table
column — the aggregate table plus the existing per-event detail modal
already answer "which mode is used most" and "tokens per mode" without
needing per-row badges the way MCP servers did.

**Why:** Directly requested. Investigated before building: found the
literal `"type":"mode"` transcript entries first, but they were always
`"normal"` in every real transcript checked — a red herring, unrelated
to the actual permission-mode concept. The real field
(`permissionMode`) took a second, broader search to find. Verified the
mode-carries-forward-from-before-the-window edge case with a synthetic
transcript before trusting it, same as every other transcript-parsing
addition in this system.

## 30. Replacing the custom dashboard with Grafana

**Decision:** Standing up Grafana (`grafana/grafana:11.3.0`, added to
`docker-compose.yml`) against the existing Postgres, provisioned
entirely as code — `grafana/provisioning/datasources/postgres.yml`
(datasource) and `grafana/provisioning/dashboards/` (a 10-panel
dashboard: 5 stat tiles, an events-by-skill bar chart, MCP usage
table, permission-mode usage table, Specs table, recent-events table —
each reusing the same SQL already written for the FastAPI dashboard's
endpoints). Intended to fully replace `server/app/static/dashboard.html`
per explicit direction, not run alongside it.

**Why:** Requested directly, after comparing Grafana against Metabase
and Superset for this specific case — a single Postgres table with
the valuable data (`mcp_usage`, `mode_usage`) inside JSONB arrays
needing `jsonb_array_elements()` unnesting. Grafana's raw-SQL-per-panel
model meant the existing queries ported over directly; it also adds
historical trending and alerting, neither of which the hand-built
dashboard had.

**Verified, not assumed:** queried the datasource through Grafana's
own `/api/ds/query` endpoint and confirmed the MCP/mode/specs numbers
matched the FastAPI API's own responses exactly. Then wiped Grafana's
own data volume entirely and restarted from zero to confirm the
dashboard and datasource load purely from the provisioned files
(`provisionedExternalId` confirmed the source), not from something
left over in Grafana's runtime state — the actual test of "is this
reproducible on a fresh clone."

**Not yet done:** click-to-filter / drill-down (dashboard template
variables for skill/spec/MCP-server/mode) — deferred to keep this
first pass fast, per direction to "see what we get out of this" before
investing further. The old dashboard's filtering UX isn't replicated
yet. Also not yet decided: whether to delete
`server/app/static/dashboard.html` and its supporting endpoints now,
or wait for confirmation this covers everything needed first.

## 31. Grafana drill-down, trend panels, and time-range filtering

**Decision:** Extended the Grafana dashboard from 10 to 13 panels and
added 4 template variables (skill, correlation_id, mcp_server, mode)
that filter every panel at once — the requested drill-down. Also
retrofitted `$__timeFilter(event_timestamp)` into every query, since
until now *none* of the panels respected Grafana's own time-range
picker at all (a real gap for something meant to behave like an
observability tool). Added 3 new time-series panels (duration trend by
skill, token trend by skill, cumulative event activity) — real
historical trending, which the old hand-built dashboard could never
do.

**Why the filter uses a sentinel-OR pattern, not `IN ($var)`:**
Grafana's standard multi-value convention (`column IN ($var)`, "All"
expanding to every real value) is the well-documented default, but it
doesn't compose with an EXISTS-based filter over an optional nested
JSONB array. Under "All," `IN ($mcp_server)` would expand to
`IN ('figma','atlassian')`, and the EXISTS check would still be
required to be true — incorrectly hiding every event with *no*
`mcp_usage` at all, when "All" should mean "don't filter, show
everything." Verified this concretely against real Postgres data
before choosing between the two approaches: the `IN` pattern silently
drops non-MCP events under "All"; the `('$var' = '__ALL__' OR ...)`
pattern returns all 24 events correctly for "All" and correctly
narrows to 1 for a specific value. Used the sentinel pattern
everywhere for consistency, even for the simple `skill_name`/
`correlation_id` filters where `IN` would have been fine on its own.

**Also found:** the Grafana admin password had changed from the
`docker-compose.yml` default (`admin`/`admin`) — the user had logged
into the live instance directly since it was first stood up. Did not
reset it; used the real current credentials instead. Deliberately
**did not** repeat the earlier "wipe the Grafana volume, restart from
scratch" reproducibility test this time, since that volume now holds
real user-made changes (at minimum the password) — wiping it again
would destroy state that wasn't there (and disposable) the first time.

**Not verified:** the actual browser drill-down interaction (selecting
a dropdown value and watching panels update) — no browser available
here. Verified everything one level down instead: the variable
population queries return correct real values, the filter SQL behaves
correctly for both "All" and specific-value cases when tested directly
against Postgres, and the dashboard JSON (variables + panels) was
accepted by Grafana's API without error. The interpolation mechanism
itself (`$var` → literal text) is long-standing, well-documented
Grafana behavior, not something newly invented here.

## 32. Real bug: `GROUP BY server`/`GROUP BY mode` referencing quoted aliases

**Decision:** Fixed `GROUP BY server` → `GROUP BY "Server"` and
`GROUP BY mode` → `GROUP BY "Mode"` in the MCP/Mode usage panel
queries.

**Why:** User-reported: both panels errored with "column ... does not
exist." Root cause: I'd renamed the SELECT aliases to capitalized,
double-quoted names (`AS "Server"`) for nicer Grafana column headers,
but left `GROUP BY server` unquoted/lowercase. Postgres folds unquoted
identifiers to lowercase but does not case-fold quoted ones, so
`server` (unquoted) and `"Server"` (quoted) are different identifiers
— `GROUP BY server` was looking for a real column that doesn't exist.
This exists in the original FastAPI query too, but harmlessly, because
that version uses lowercase unquoted aliases throughout (`AS server`),
so unquoted `GROUP BY server` matches correctly there. The bug was
introduced specifically when adapting the query for nicer Grafana
display. **Process gap:** I verified the *query logic* earlier using
different (lowercase) aliases than what was actually deployed —
verifying an approximation instead of the exact deployed SQL is what
let this through. Reproduced the exact error, then the exact fix,
directly against Postgres before touching the dashboard again.

## 33. Bulk demo data: `scripts/seed_bulk_demo_data.py`

**Decision:** Added a third seed script — 20 specs, ~118 events,
randomized-but-plausible durations/tokens/files per skill (fixed
ranges per skill type), spread over the last 14 days (`random.seed(42)`
for reproducibility), each spec stopping at a random point in the
specify→plan→tasks→implement lifecycle (weighted toward finishing, but
not all do — realistic in-progress variety). ~30% of `implement` runs
get MCP usage (figma/atlassian/both), every completed event gets
`mode_usage` (weighted 70% pure-auto, 30% mixed with some plan-mode
turns), tokens split correctly between modes so they still sum to
`tokens_used` exactly. Cleared the old 3-spec curated dataset first
rather than mixing it in, then updated the dashboard's default time
range from `now-6h` to `now-15d` to actually cover the new spread —
otherwise the trend panels would've shown almost nothing by default.

**Why:** Directly requested — the trend panels looked sparse with only
3 specs over 3 hours (2-4 points per series). This is different from
`seed_demo_data.py` (3 curated specs, each telling one clear story —
kept for that purpose) and the README's one-event-pair smoke test.

**Verified:** re-queried the implement token trend after seeding — 8
points now vs. 2 before; MCP/mode aggregate totals moved from
thousands to tens-of-thousands, confirmed via Grafana's own query API
against the real dashboard time range, not assumed from the script's
own printed summary.
