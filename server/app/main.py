import os
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row
from psycopg.types.json import Json
from pydantic import BaseModel, Field

from .db import get_conn

app = FastAPI(title="Spec-kit Telemetry API")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class EventIn(BaseModel):
    event_type: str
    correlation_id: str
    skill_name: str
    repository: str
    branch: Optional[str] = None
    ticket_id: Optional[str] = None
    timestamp: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/v1/events", status_code=201)
def create_event(event: EventIn, conn=Depends(get_conn)):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            INSERT INTO events
                (event_type, correlation_id, skill_name, repository, branch,
                 ticket_id, event_timestamp, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, received_at
            """,
            (
                event.event_type,
                event.correlation_id,
                event.skill_name,
                event.repository,
                event.branch,
                event.ticket_id,
                event.timestamp.astimezone(timezone.utc),
                Json(event.metadata),
            ),
        )
        row = cur.fetchone()
        conn.commit()
    return {"id": row["id"], "received_at": row["received_at"]}


@app.get("/api/v1/events")
def list_events(
    limit: int = 50,
    skill_name: Optional[str] = None,
    correlation_id: Optional[str] = None,
    mcp_server: Optional[str] = None,
    mode: Optional[str] = None,
    conn=Depends(get_conn),
):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, event_type, correlation_id, skill_name, repository,
                   branch, ticket_id, event_timestamp, received_at, metadata
            FROM events
            WHERE (%(skill_name)s::text IS NULL OR skill_name = %(skill_name)s)
              AND (%(correlation_id)s::text IS NULL OR correlation_id = %(correlation_id)s)
              AND (
                    %(mcp_server)s::text IS NULL
                    OR EXISTS (
                        SELECT 1 FROM jsonb_array_elements(coalesce(metadata->'mcp_usage', '[]'::jsonb)) elem
                        WHERE elem->>'server' = %(mcp_server)s
                    )
              )
              AND (
                    %(mode)s::text IS NULL
                    OR EXISTS (
                        SELECT 1 FROM jsonb_array_elements(coalesce(metadata->'mode_usage', '[]'::jsonb)) elem
                        WHERE elem->>'mode' = %(mode)s
                    )
              )
            ORDER BY event_timestamp DESC, received_at DESC
            LIMIT %(limit)s
            """,
            {
                "skill_name": skill_name,
                "correlation_id": correlation_id,
                "mcp_server": mcp_server,
                "mode": mode,
                "limit": limit,
            },
        )
        return cur.fetchall()


@app.get("/api/v1/mcp-stats")
def mcp_stats(conn=Depends(get_conn)):
    """Aggregate MCP server usage across all events — calls, attributed
    tokens, and how many distinct events/specs touched each server."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT
                elem->>'server' AS server,
                sum((elem->>'calls')::int) AS calls,
                sum((elem->>'tokens')::bigint) AS tokens,
                count(DISTINCT events.id) AS event_count,
                count(DISTINCT events.correlation_id) AS spec_count
            FROM events, jsonb_array_elements(metadata->'mcp_usage') AS elem
            GROUP BY server
            ORDER BY tokens DESC
            """
        )
        return cur.fetchall()


@app.get("/api/v1/mode-stats")
def mode_stats(conn=Depends(get_conn)):
    """Aggregate permission-mode usage across all events — turns,
    attributed tokens, and how many distinct events/specs used each mode."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT
                elem->>'mode' AS mode,
                sum((elem->>'turns')::int) AS turns,
                sum((elem->>'tokens')::bigint) AS tokens,
                count(DISTINCT events.id) AS event_count,
                count(DISTINCT events.correlation_id) AS spec_count
            FROM events, jsonb_array_elements(metadata->'mode_usage') AS elem
            GROUP BY mode
            ORDER BY tokens DESC
            """
        )
        return cur.fetchall()


@app.get("/api/v1/specs")
def list_specs(conn=Depends(get_conn)):
    """One row per correlation_id — the per-spec lifecycle view. Used for
    the iteration-count question and as a filter picker on the dashboard."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT
                correlation_id,
                max(ticket_id) AS ticket_id,
                max(repository) AS repository,
                max(branch) AS branch,
                min(event_timestamp) AS first_event_at,
                max(event_timestamp) AS last_event_at,
                array_agg(DISTINCT skill_name ORDER BY skill_name) AS skills,
                count(*) FILTER (WHERE event_type LIKE '%%_started') AS total_runs,
                count(*) FILTER (WHERE event_type LIKE '%%_completed') AS total_completed,
                sum((metadata->>'duration_seconds')::float) AS total_duration_seconds,
                sum((metadata->>'tokens_used')::bigint) AS total_tokens,
                max(metadata->>'summary') FILTER (WHERE event_type = 'speckit_specify_started') AS summary
            FROM events
            GROUP BY correlation_id
            ORDER BY max(event_timestamp) DESC
            """
        )
        return cur.fetchall()


@app.get("/api/v1/stats")
def stats(
    skill_name: Optional[str] = None,
    correlation_id: Optional[str] = None,
    mcp_server: Optional[str] = None,
    mode: Optional[str] = None,
    conn=Depends(get_conn),
):
    filters = {
        "skill_name": skill_name,
        "correlation_id": correlation_id,
        "mcp_server": mcp_server,
        "mode": mode,
    }
    filter_sql = """
        (%(skill_name)s::text IS NULL OR skill_name = %(skill_name)s)
        AND (%(correlation_id)s::text IS NULL OR correlation_id = %(correlation_id)s)
        AND (
              %(mcp_server)s::text IS NULL
              OR EXISTS (
                  SELECT 1 FROM jsonb_array_elements(coalesce(metadata->'mcp_usage', '[]'::jsonb)) elem
                  WHERE elem->>'server' = %(mcp_server)s
              )
        )
        AND (
              %(mode)s::text IS NULL
              OR EXISTS (
                  SELECT 1 FROM jsonb_array_elements(coalesce(metadata->'mode_usage', '[]'::jsonb)) elem
                  WHERE elem->>'mode' = %(mode)s
              )
        )
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT count(*) AS total FROM events WHERE {filter_sql}", filters)
        total = cur.fetchone()["total"]

        cur.execute(
            f"""
            SELECT count(*) AS total FROM events
            WHERE event_type LIKE '%%_completed' AND {filter_sql}
            """,
            filters,
        )
        total_completed = cur.fetchone()["total"]

        cur.execute(
            f"""
            SELECT sum((metadata->>'duration_seconds')::float) AS total_seconds,
                   avg((metadata->>'duration_seconds')::float) AS avg_seconds,
                   sum((metadata->>'tokens_used')::bigint) AS total_tokens
            FROM events
            WHERE event_type LIKE '%%_completed' AND {filter_sql}
            """,
            filters,
        )
        duration_row = cur.fetchone()

        # Bar chart is always the global overview, regardless of the filter,
        # so there's always something to click back to.
        cur.execute(
            """
            SELECT skill_name, count(*) AS count
            FROM events
            GROUP BY skill_name
            ORDER BY skill_name
            """
        )
        by_skill = cur.fetchall()

        cur.execute(
            """
            SELECT skill_name,
                   avg((metadata->>'duration_seconds')::float) AS avg_seconds
            FROM events
            WHERE event_type LIKE '%%_completed'
            GROUP BY skill_name
            """
        )
        avg_duration_by_skill = cur.fetchall()

    return {
        "total_events": total,
        "total_completed": total_completed,
        "total_duration_seconds": duration_row["total_seconds"],
        "avg_duration_seconds": duration_row["avg_seconds"],
        "total_tokens": duration_row["total_tokens"],
        "by_skill": by_skill,
        "avg_duration_by_skill": avg_duration_by_skill,
    }


@app.get("/dashboard")
def dashboard():
    return FileResponse(os.path.join(STATIC_DIR, "dashboard.html"))
