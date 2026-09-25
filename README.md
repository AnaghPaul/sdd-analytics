# sdd-analytics

Telemetry for Spec-kit skill usage. When a developer runs a Spec-kit
skill (`/speckit-specify`, `/speckit-plan`, `/speckit-tasks`,
`/speckit-implement`) in an instrumented repo, this logs a "started"
and "completed" event to a small database — so questions like "how
long does a spec take," "which skills get used," and "how many
iterations does Claude need" can be answered from real data instead of
guesswork. See [`core-idea.md`](core-idea.md) for the original
motivation.

## Quickstart

### 1. Prerequisites (first time on a new machine)

```bash
# macOS, via Homebrew — Colima runs Docker without Docker Desktop
brew install colima docker docker-compose
colima start
```

Already have Docker Desktop instead? Skip `colima start` — just make
sure Docker is running. You'll also need Python 3 with `venv` support
(ships with macOS/most Linux distros).

### 2. Clone and set up

```bash
git clone <this-repo-url>
cd sdd-analytics
cd server && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && cd ..
```

### 3. Start everything

```bash
./start.sh
```

Starts Colima (if needed), Postgres + Grafana, waits for Postgres to
be ready, then runs the telemetry API in the foreground. Leave it
running — `Ctrl+C` stops the API; Postgres/Grafana keep running in the
background (`docker-compose down` to stop those too).

### 4. Verify it's up

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

Open:
- **Grafana** — http://localhost:3000 (`admin`/`admin` on a fresh
  volume — change it if this ever runs anywhere but your own machine)
- **Plain HTML dashboard** — http://localhost:8000/dashboard

The database starts **empty** — nothing is seeded automatically. To
see either dashboard populated with realistic data before wiring up
your own repo, see
[`docs/guide.md` → "Seeing it with demo data"](docs/guide.md#seeing-it-with-demo-data).

## Where to go next

This README is deliberately just the quickstart. For everything else:

- **[`docs/guide.md`](docs/guide.md)** — architecture, the event
  schema, the Grafana dashboard in detail, seeding demo data,
  instrumenting your own Spec-kit repo, troubleshooting, and current
  known limitations.
- **[`docs/decisions.md`](docs/decisions.md)** — the running log of
  design decisions and why they were made.
- **[`docs/edge-cases.md`](docs/edge-cases.md)** — specific
  failure-mode scenarios found along the way and their status.
