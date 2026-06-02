from __future__ import annotations

import os
import sys
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from fastmcp import FastMCP


SERVER_NAME = "codex2opencode"
DEFAULT_TIMEOUT_SEC = 180
MAX_TIMEOUT_SEC = 900
WORKSPACE_ROOT = Path(__file__).resolve().parent
STATE_DIR = WORKSPACE_ROOT / ".codex2opencode"
STATE_FILE = STATE_DIR / "work-sessions.json"

mcp = FastMCP(
    name=SERVER_NAME,
    instructions=(
        "Use this server to ask OpenCode for a second opinion on coding tasks. "
        "Prefer focused prompts: reviews, debugging hypotheses, design alternatives, "
        "or implementation advice."
    ),
)


def _resolve_cwd(cwd: str | None) -> Path:
    candidate = WORKSPACE_ROOT if not cwd else Path(cwd).expanduser()
    if not candidate.is_absolute():
        candidate = WORKSPACE_ROOT / candidate

    resolved = candidate.resolve()
    if not resolved.is_dir():
        raise ValueError(f"cwd does not exist or is not a directory: {resolved}")

    if resolved != WORKSPACE_ROOT and WORKSPACE_ROOT not in resolved.parents:
        raise ValueError(f"cwd must be inside workspace root: {WORKSPACE_ROOT}")

    return resolved


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _opencode_executable() -> str | None:
    names = ["opencode.cmd", "opencode.exe", "opencode"] if sys.platform == "win32" else ["opencode"]
    for name in names:
        path = shutil.which(name)
        if path:
            return path
    return None


def _build_opencode_args(
    prompt: str,
    *,
    executable: str = "opencode",
    agent: str | None = None,
    model: str | None = None,
    attach_url: str | None = None,
    session_id: str | None = None,
    format_json: bool = False,
) -> list[str]:
    args = [executable, "run"]

    attach_url = _clean_optional(attach_url)
    model = _clean_optional(model)
    agent = _clean_optional(agent)
    session_id = _clean_optional(session_id)

    if attach_url:
        args.extend(["--attach", attach_url])
    if session_id:
        args.extend(["--session", session_id])
    if model:
        args.extend(["--model", model])
    if agent:
        args.extend(["--agent", agent])
    if format_json:
        args.extend(["--format", "json"])

    args.append(prompt)
    return args


def _read_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {"active_work_id": None, "works": {}}
    with STATE_FILE.open("r", encoding="utf-8") as f:
        state = json.load(f)
    state.setdefault("active_work_id", None)
    state.setdefault("works", {})
    return state


def _write_state(state: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _parse_json_events(stdout: str) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    text_parts: list[str] = []
    session_id: str | None = None

    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        events.append(event)
        session_id = session_id or event.get("sessionID")

        part = event.get("part")
        if isinstance(part, dict):
            session_id = session_id or part.get("sessionID")
            if part.get("type") == "text" and isinstance(part.get("text"), str):
                text_parts.append(part["text"])

    return {
        "session_id": session_id,
        "text": "".join(text_parts),
        "events": events,
    }


def _run_opencode(
    prompt: str,
    *,
    cwd: str | None = None,
    agent: str | None = None,
    model: str | None = None,
    attach_url: str | None = None,
    session_id: str | None = None,
    format_json: bool = False,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> dict[str, Any]:
    prompt = prompt.strip()
    if not prompt:
        raise ValueError("prompt must not be empty")

    if timeout_sec < 1 or timeout_sec > MAX_TIMEOUT_SEC:
        raise ValueError(f"timeout_sec must be between 1 and {MAX_TIMEOUT_SEC}")

    executable = _opencode_executable()
    if executable is None:
        raise RuntimeError("opencode was not found on PATH")

    resolved_cwd = _resolve_cwd(cwd)
    args = _build_opencode_args(
        prompt,
        executable=executable,
        agent=agent,
        model=model,
        attach_url=attach_url,
        session_id=session_id,
        format_json=format_json,
    )

    try:
        completed = subprocess.run(
            args,
            cwd=resolved_cwd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "cwd": str(resolved_cwd),
            "args": args,
            "exit_code": None,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "error": f"opencode timed out after {timeout_sec} seconds",
        }

    return {
        "ok": completed.returncode == 0,
        "cwd": str(resolved_cwd),
        "args": args,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "parsed": _parse_json_events(completed.stdout) if format_json else None,
    }


@mcp.tool()
def opencode_ask(
    prompt: str,
    cwd: str | None = None,
    agent: str | None = None,
    model: str | None = None,
    attach_url: str | None = None,
    session_id: str | None = None,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Ask OpenCode a focused question through `opencode run`."""
    return _run_opencode(
        prompt,
        cwd=cwd,
        agent=agent,
        model=model,
        attach_url=attach_url,
        session_id=session_id,
        timeout_sec=timeout_sec,
    )


@mcp.tool()
def opencode_work_start(
    work_id: str,
    prompt: str,
    cwd: str | None = None,
    agent: str | None = None,
    model: str | None = None,
    attach_url: str | None = None,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Start one OpenCode session for a named unit of work and remember it."""
    work_id = work_id.strip()
    if not work_id:
        raise ValueError("work_id must not be empty")

    state = _read_state()
    if work_id in state["works"]:
        raise ValueError(f"work_id already exists: {work_id}")

    result = _run_opencode(
        prompt,
        cwd=cwd,
        agent=agent,
        model=model,
        attach_url=attach_url,
        format_json=True,
        timeout_sec=timeout_sec,
    )

    parsed = result.get("parsed") or {}
    session_id = parsed.get("session_id")
    if result["ok"] and not session_id:
        raise RuntimeError("OpenCode completed but no session_id was found in JSON output")

    if result["ok"]:
        state["works"][work_id] = {
            "session_id": session_id,
            "cwd": result["cwd"],
            "agent": _clean_optional(agent),
            "model": _clean_optional(model),
            "attach_url": _clean_optional(attach_url),
        }
        state["active_work_id"] = work_id
        _write_state(state)

    result["work_id"] = work_id
    result["session_id"] = session_id
    result["text"] = parsed.get("text", "")
    return result


@mcp.tool()
def opencode_work_ask(
    prompt: str,
    work_id: str | None = None,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Continue the OpenCode session for the current or specified unit of work."""
    state = _read_state()
    resolved_work_id = _clean_optional(work_id) or state.get("active_work_id")
    if not resolved_work_id:
        raise ValueError("No active work_id. Call opencode_work_start first.")

    work = state["works"].get(resolved_work_id)
    if not work:
        raise ValueError(f"Unknown work_id: {resolved_work_id}")

    result = _run_opencode(
        prompt,
        cwd=work.get("cwd"),
        agent=work.get("agent"),
        model=work.get("model"),
        attach_url=work.get("attach_url"),
        session_id=work["session_id"],
        format_json=True,
        timeout_sec=timeout_sec,
    )

    parsed = result.get("parsed") or {}
    state["active_work_id"] = resolved_work_id
    _write_state(state)

    result["work_id"] = resolved_work_id
    result["session_id"] = work["session_id"]
    result["text"] = parsed.get("text", "")
    return result


@mcp.tool()
def opencode_work_list() -> dict[str, Any]:
    """List remembered OpenCode work sessions."""
    return _read_state()


@mcp.tool()
def opencode_work_end(work_id: str | None = None) -> dict[str, Any]:
    """Forget a remembered OpenCode work session without deleting it from OpenCode."""
    state = _read_state()
    resolved_work_id = _clean_optional(work_id) or state.get("active_work_id")
    if not resolved_work_id:
        raise ValueError("No active work_id to end")

    removed = state["works"].pop(resolved_work_id, None)
    if state.get("active_work_id") == resolved_work_id:
        state["active_work_id"] = None
    _write_state(state)

    return {
        "ended": removed is not None,
        "work_id": resolved_work_id,
        "session_id": removed.get("session_id") if removed else None,
    }


@mcp.tool()
def opencode_status() -> dict[str, Any]:
    """Check whether the OpenCode CLI is available to this MCP server."""
    path = _opencode_executable()
    if path is None:
        return {"available": False, "path": None, "version": None}

    completed = subprocess.run(
        [path, "--version"],
        cwd=WORKSPACE_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return {
        "available": completed.returncode == 0,
        "path": path,
        "version": completed.stdout.strip() or completed.stderr.strip(),
        "exit_code": completed.returncode,
    }


if __name__ == "__main__":
    mcp.run()
