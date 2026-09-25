#!/usr/bin/env python3
"""Spec-kit telemetry hook.

Wired into a repo's .claude/settings.json for PostToolUse (matcher: Skill)
and Stop. Fire-and-forget: never blocks the developer, never raises.
"""
import json
import os
import re
import subprocess
import sys
import urllib.request
import uuid
from datetime import datetime, timezone

TELEMETRY_URL = os.environ.get(
    "TELEMETRY_API_URL", "http://localhost:8000/api/v1/events"
)
TIMEOUT_SECONDS = 2

IN_SCOPE_SKILLS = {"speckit-specify", "speckit-plan", "speckit-tasks", "speckit-implement"}
SPEC_CREATING_SKILL = "speckit-specify"
AGENT_NAME = "claude"
FILE_TOOLS = {"Read", "Edit", "Write"}

STATE_FILENAME = ".telemetry-state.json"
MARKER_FILENAME = ".telemetry.json"
SUMMARY_MAX_LEN = 500

# Jira-style ticket key, e.g. ABC-123, BC-633.
TICKET_ID_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b")


def log(msg: str) -> None:
    sys.stderr.write(f"[telemetry_hook] {msg}\n")


def run_git(args: list[str], cwd: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=2
        )
        return result.stdout.strip() or None
    except Exception:
        return None


def repo_context(cwd: str) -> tuple[str | None, str | None, str | None]:
    """Returns (repo_root, repository_name, branch).

    Prefers $CLAUDE_PROJECT_DIR (fixed at session launch) over deriving
    repo_root from `cwd` via `git rev-parse --show-toplevel`. The latter
    breaks if the session's tracked working directory has drifted into a
    nested git repo (e.g. a submodule under src/) at any point — it would
    resolve to the submodule's root instead of the actual project root."""
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir and os.path.isdir(os.path.join(project_dir, ".git")):
        repo_root = project_dir
    else:
        repo_root = run_git(["rev-parse", "--show-toplevel"], cwd)
    if not repo_root:
        return None, None, None
    repository_name = os.path.basename(repo_root)
    branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_root)
    return repo_root, repository_name, branch


def current_feature_dir_abs(repo_root: str) -> str | None:
    """Reads .specify/feature.json *live* — only safe for skills that don't
    themselves change which feature is "current" (plan/tasks/implement).
    For speckit-specify, this is stale at start time by design."""
    feature_json_path = os.path.join(repo_root, ".specify", "feature.json")
    try:
        with open(feature_json_path) as f:
            data = json.load(f)
        feature_directory = data.get("feature_directory")
        if feature_directory:
            return os.path.join(repo_root, feature_directory.rstrip("/"))
    except Exception:
        pass
    return None


def read_marker(feature_dir_abs: str) -> dict:
    try:
        with open(os.path.join(feature_dir_abs, MARKER_FILENAME)) as f:
            return json.load(f)
    except Exception:
        return {}


def write_marker(feature_dir_abs: str, corr_id: str, ticket_id: str | None) -> None:
    try:
        with open(os.path.join(feature_dir_abs, MARKER_FILENAME), "w") as f:
            json.dump({"correlation_id": corr_id, "ticket_id": ticket_id}, f)
    except Exception as e:
        log(f"failed to write marker into {feature_dir_abs}: {e}")


def resolve_existing_context(repo_root: str) -> tuple[str | None, str | None]:
    """For plan/tasks/implement: feature.json is stable during their run, so
    reading it live is safe. correlation_id/ticket_id come from the marker
    file speckit-specify coupled to this spec; self-heals older specs that
    predate the marker by falling back to the folder name (with no ticket
    id, since there's nothing to recover that from)."""
    feature_dir_abs = current_feature_dir_abs(repo_root)
    if not feature_dir_abs:
        return None, None
    marker = read_marker(feature_dir_abs)
    if marker.get("correlation_id"):
        return marker["correlation_id"], marker.get("ticket_id")
    fallback = os.path.basename(feature_dir_abs)
    write_marker(feature_dir_abs, fallback, None)
    return fallback, None


def extract_ticket_id(text: str) -> str | None:
    match = TICKET_ID_RE.search(text or "")
    return match.group(0) if match else None


def state_file_path(repo_root: str) -> str:
    return os.path.join(repo_root, ".claude", STATE_FILENAME)


def read_speckit_version(repo_root: str) -> str | None:
    try:
        with open(os.path.join(repo_root, ".specify", "init-options.json")) as f:
            return json.load(f).get("speckit_version")
    except Exception:
        return None


def latest_model(transcript_path: str | None) -> str | None:
    """Best-effort scan of the whole transcript for the most recent model
    name — used at skill-start time, when there's no [start, end] window
    yet to scope the search to."""
    if not transcript_path or not os.path.exists(transcript_path):
        return None
    model = None
    try:
        with open(transcript_path) as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                m = (entry.get("message") or {}).get("model")
                if m:
                    model = m
    except Exception:
        pass
    return model


def analyze_transcript_window(transcript_path: str | None, start: datetime, end: datetime) -> dict:
    """Single pass over the transcript JSONL for everything derivable from
    assistant turns within [start, end]: summed input+output tokens, count
    of distinct files touched (Read/Edit/Write), the model in use,
    per-MCP-server usage (calls + attributed tokens), and per-permission-mode
    usage (turns + attributed tokens). Best-effort — returns an empty dict
    on any failure so a parsing issue never blocks the (already
    fire-and-forget) event."""
    result: dict = {}
    if not transcript_path or not os.path.exists(transcript_path):
        return result
    total_tokens = 0
    found_tokens = False
    files: set[str] = set()
    model = None
    mcp_calls: dict[str, int] = {}
    mcp_tokens: dict[str, int] = {}
    mode_turns: dict[str, int] = {}
    mode_tokens: dict[str, int] = {}
    # "permission-mode" entries (auto/default/plan) have no timestamp of
    # their own, so mode is tracked as running state across the *whole*
    # file in order, not scoped to [start, end] — otherwise a mode set
    # before the window started would look unknown.
    current_mode: str | None = None
    try:
        with open(transcript_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if entry.get("type") == "permission-mode":
                    current_mode = entry.get("permissionMode") or current_mode
                    continue
                if entry.get("type") != "assistant":
                    continue
                ts = entry.get("timestamp")
                if not ts:
                    continue
                try:
                    entry_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except Exception:
                    continue
                if not (start <= entry_time <= end):
                    continue
                message = entry.get("message") or {}
                if message.get("model"):
                    model = message["model"]
                usage = message.get("usage") or entry.get("usage")
                turn_tokens = 0
                if usage:
                    turn_tokens = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
                    total_tokens += turn_tokens
                    found_tokens = True

                servers_this_turn: set[str] = set()
                for block in message.get("content") or []:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    name = block.get("name") or ""
                    if name in FILE_TOOLS:
                        path = (block.get("input") or {}).get("file_path")
                        if path:
                            files.add(path)
                    elif name.startswith("mcp__"):
                        server = name.split("__")[1]
                        mcp_calls[server] = mcp_calls.get(server, 0) + 1
                        servers_this_turn.add(server)
                # Attribute this turn's tokens to each distinct MCP server it
                # touched (once per server per turn, not per call) — the
                # closest honest approximation available, since usage is only
                # reported per turn, not per individual tool call.
                for server in servers_this_turn:
                    mcp_tokens[server] = mcp_tokens.get(server, 0) + turn_tokens

                if current_mode:
                    mode_turns[current_mode] = mode_turns.get(current_mode, 0) + 1
                    mode_tokens[current_mode] = mode_tokens.get(current_mode, 0) + turn_tokens
    except Exception as e:
        log(f"failed to analyze transcript: {e}")
        return result

    if found_tokens:
        result["tokens_used"] = total_tokens
    if files:
        result["files_considered"] = len(files)
    if model:
        result["model"] = model
    if mcp_calls:
        result["mcp_usage"] = [
            {"server": server, "calls": mcp_calls[server], "tokens": mcp_tokens.get(server, 0)}
            for server in sorted(mcp_calls)
        ]
    if mode_turns:
        result["mode_usage"] = [
            {"mode": mode, "turns": mode_turns[mode], "tokens": mode_tokens.get(mode, 0)}
            for mode in sorted(mode_turns)
        ]
    return result


def post_event(payload: dict) -> None:
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            TELEMETRY_URL,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS)
    except Exception as e:
        log(f"failed to send event (dropped): {e}")


def finalize_run(repo_root: str, state: dict, completed_at: datetime) -> None:
    """Posts the *_completed event for an in-flight state and, for specify,
    couples its correlation/ticket id to the spec via the marker file."""
    if state["skill"] == SPEC_CREATING_SKILL:
        feature_dir_abs = current_feature_dir_abs(repo_root)
        if feature_dir_abs:
            write_marker(feature_dir_abs, state["correlation_id"], state.get("ticket_id"))

    started_at = datetime.fromisoformat(state["started_at"])
    duration_seconds = (completed_at - started_at).total_seconds()
    analysis = analyze_transcript_window(state.get("transcript_path"), started_at, completed_at)

    metadata = {
        "duration_seconds": duration_seconds,
        "agent_name": AGENT_NAME,
        "agent_version": analysis.get("model") or state.get("agent_version"),
        "skill_version": read_speckit_version(repo_root),
    }
    if state.get("summary"):
        metadata["summary"] = state["summary"]
    if "tokens_used" in analysis:
        metadata["tokens_used"] = analysis["tokens_used"]
    if "files_considered" in analysis:
        metadata["files_considered"] = analysis["files_considered"]
    if "mcp_usage" in analysis:
        metadata["mcp_usage"] = analysis["mcp_usage"]
    if "mode_usage" in analysis:
        metadata["mode_usage"] = analysis["mode_usage"]

    phase = state["skill"].removeprefix("speckit-")
    post_event(
        {
            "event_type": f"speckit_{phase}_completed",
            "correlation_id": state["correlation_id"],
            "skill_name": state["skill"],
            "repository": state["repository"],
            "branch": state["branch"],
            "ticket_id": state.get("ticket_id"),
            "timestamp": completed_at.isoformat(),
            "metadata": metadata,
        }
    )


def handle_post_tool_use(payload: dict) -> None:
    if payload.get("tool_name") != "Skill":
        return
    tool_input = payload.get("tool_input", {})
    skill_name = tool_input.get("skill") or tool_input.get("name")
    if skill_name not in IN_SCOPE_SKILLS:
        if skill_name is None:
            log(f"could not find skill name in tool_input, got: {tool_input!r}")
        return

    cwd = payload.get("cwd", os.getcwd())
    repo_root, repository, branch = repo_context(cwd)
    if not repo_root:
        log("not a git repo, skipping")
        return

    # If a skill is still marked in-flight, the fact that a *new* skill just
    # started means the previous one must have already finished — Stop only
    # fires once per turn, so multiple skills chained in one turn (no user
    # interaction in between) would otherwise silently clobber each other's
    # state and lose the earlier ones' completed events entirely.
    existing_state_path = state_file_path(repo_root)
    if os.path.exists(existing_state_path):
        try:
            with open(existing_state_path) as f:
                prev_state = json.load(f)
            finalize_run(repo_root, prev_state, datetime.now(timezone.utc))
            log(f"implicitly completed in-flight '{prev_state.get('skill')}' — a new skill started before Stop fired")
        except Exception as e:
            log(f"failed to finalize stale state file: {e}")

    args_text = (tool_input.get("args") or "").strip()
    if not args_text:
        log(f"no 'args' found in tool_input for {skill_name}, got keys: {list(tool_input.keys())}")
    summary = args_text[:SUMMARY_MAX_LEN]

    if skill_name == SPEC_CREATING_SKILL:
        # feature.json is guaranteed stale here — specify hasn't created/
        # registered the spec folder yet. Mint a fresh id instead; it gets
        # coupled to whichever spec folder is current once specify finishes.
        corr_id = str(uuid.uuid4())
        ticket_id = extract_ticket_id(args_text)
    else:
        corr_id, ticket_id = resolve_existing_context(repo_root)
        if not corr_id:
            log("no .specify/feature.json found, skipping")
            return

    transcript_path = payload.get("transcript_path")
    agent_version = latest_model(transcript_path)
    skill_version = read_speckit_version(repo_root)

    started_at = datetime.now(timezone.utc)
    state = {
        "skill": skill_name,
        "correlation_id": corr_id,
        "ticket_id": ticket_id,
        "summary": summary,
        "repository": repository,
        "branch": branch,
        "started_at": started_at.isoformat(),
        "transcript_path": transcript_path,
        "agent_version": agent_version,
    }
    with open(state_file_path(repo_root), "w") as f:
        json.dump(state, f)

    metadata = {
        "agent_name": AGENT_NAME,
        "agent_version": agent_version,
        "skill_version": skill_version,
    }
    if summary:
        metadata["summary"] = summary

    phase = skill_name.removeprefix("speckit-")
    post_event(
        {
            "event_type": f"speckit_{phase}_started",
            "correlation_id": corr_id,
            "skill_name": skill_name,
            "repository": repository,
            "branch": branch,
            "ticket_id": ticket_id,
            "timestamp": started_at.isoformat(),
            "metadata": metadata,
        }
    )


def handle_stop(payload: dict) -> None:
    cwd = payload.get("cwd", os.getcwd())
    repo_root, _, _ = repo_context(cwd)
    if not repo_root:
        return

    path = state_file_path(repo_root)
    if not os.path.exists(path):
        return

    with open(path) as f:
        state = json.load(f)
    os.remove(path)
    finalize_run(repo_root, state, datetime.now(timezone.utc))


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception as e:
        log(f"bad stdin payload: {e}")
        return

    event_name = payload.get("hook_event_name")
    try:
        if event_name == "PostToolUse":
            handle_post_tool_use(payload)
        elif event_name == "Stop":
            handle_stop(payload)
    except Exception as e:
        log(f"unhandled error: {e}")


if __name__ == "__main__":
    main()
