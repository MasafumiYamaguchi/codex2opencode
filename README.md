# codex2opencode

Personal FastMCP bridge for asking OpenCode from Codex.

## Tools

This exposes a local stdio MCP server with these tools:

- `opencode_ask`: runs `opencode run` with a focused prompt.
- `opencode_status`: checks whether the OpenCode CLI is available.
- `opencode_work_start`: starts a named unit of work and remembers the OpenCode session ID.
- `opencode_work_ask`: continues the current or specified unit of work with `--session`.
- `opencode_work_list`: lists remembered work sessions.
- `opencode_work_end`: forgets a remembered work session without deleting it from OpenCode.

The bridge validates `cwd` so OpenCode can only be launched inside allowed roots.
Allowed roots are:

- this repository,
- projects marked `trust_level = "trusted"` in Codex `config.toml`,
- paths listed in `CODEX2OPENCODE_ALLOWED_ROOTS`.

Remembered work sessions are stored in `.codex2opencode/work-sessions.json`.

## Work Session Summaries

Each remembered work session tracks:

- `session_id`: the OpenCode session ID used for follow-ups.
- `cwd`, `agent`, `model`, `attach_url`: the values that were used to start it.
- `created_at`: ISO 8601 timestamp of the first start.
- `last_used_at`: ISO 8601 timestamp of the most recent call against it.
- `turn_count`: how many times the session has been used (start, resume, or follow-up).

`opencode_work_list` returns the raw `works` state for backward compatibility
and adds a `summaries` list sorted by `last_used_at` descending, so the most
recent sessions appear first.

## Default `cwd`

When a tool is called without an explicit `cwd`, the bridge falls back to:

1. `CODEX2OPENCODE_DEFAULT_CWD` if set (must resolve to a directory inside an
   allowed root).
2. This repository's root.

Set `CODEX2OPENCODE_DEFAULT_CWD` in the MCP server env block so external
repositories no longer need an explicit `cwd` on every call.

## Invocation Logs

Every `opencode_ask`, `opencode_work_start`, and `opencode_work_ask` call
appends one JSON line to `.codex2opencode/invocations.jsonl` containing
`timestamp`, `tool`, `work_id`, `cwd`, `exit_code`, `session_id`, `ok`,
`error`, `prompt`, and `text`.

For privacy, set `CODEX2OPENCODE_LOG_PROMPTS=false` on the MCP server env
block to redact `prompt` and `text` from new log entries. Recognized
falsy values are `false`, `0`, `no`, `off`, and an empty string. Older
log entries are not rewritten.

Logging is best-effort: a write failure is swallowed and never breaks a
tool call.

## Setup

Install dependencies:

```powershell
python -m pip install -e .[dev]
```

Run tests:

```powershell
python -m pytest
```

Run the MCP server locally:

```powershell
python server.py
```

## MCP Client Config

Register the server as a local stdio MCP server. The exact config location depends
on the client, but the command should point at this file:

```json
{
  "mcpServers": {
    "codex2opencode": {
      "command": "python",
      "args": ["C:\\Files\\prog\\codex2opencode\\server.py"]
    }
  }
}
```

To allow additional repositories, set `CODEX2OPENCODE_ALLOWED_ROOTS` on the MCP
server. Use the platform path separator: `;` on Windows, `:` on macOS/Linux.

```toml
[mcp_servers.codex2opencode.env]
CODEX2OPENCODE_ALLOWED_ROOTS = 'C:\Files\prog;D:\work'
CODEX2OPENCODE_DEFAULT_CWD = 'D:\work\current-repo'
CODEX2OPENCODE_LOG_PROMPTS = 'false'
```

## Codex Skill

This repository includes a Codex skill at `skills/codex-opencode-bridge`.

To make Codex discover it, copy or sync that directory into your Codex skills
directory:

```powershell
Copy-Item -Recurse -Force `
  .\skills\codex-opencode-bridge `
  $env:USERPROFILE\.codex\skills\codex-opencode-bridge
```

The skill tells Codex to use one OpenCode session per bug, feature, review, or
design thread, using `opencode_work_start` followed by `opencode_work_ask`.

## Tool Shape

`opencode_ask` accepts:

- `prompt`: the question to send to OpenCode.
- `cwd`: optional working directory inside an allowed root.
- `agent`: optional OpenCode agent.
- `model`: optional OpenCode model in provider/model form.
- `attach_url`: optional `opencode serve` URL for later Phase 2 usage.
- `session_id`: optional OpenCode session ID to continue.
- `timeout_sec`: timeout from 1 to 900 seconds.

`opencode_work_start` accepts everything above that applies, plus:

- `work_id`: stable, short identifier for the unit of work.
- `on_exists`: `"error"` (default), `"resume"`, or `"replace"`. Controls
  what to do when `work_id` already exists in local state:
  - `"error"` raises `ValueError` (the original strict behavior).
  - `"resume"` continues the existing OpenCode session with the new
    `prompt` and reuses the stored `cwd`, `agent`, `model`, and
    `attach_url`. The response includes `resumed: True`.
  - `"replace"` forgets the previous local reference and starts a fresh
    OpenCode session under the same `work_id`. The previous OpenCode
    session itself is left untouched. The response includes
    `replaced: True` on success.
  When `work_id` does not exist yet, `on_exists` is ignored.

## One Work, One Session

Use this workflow when Codex should keep OpenCode's memory warm for a task:

1. Call `opencode_work_start` with a stable `work_id` and the first prompt.
2. Call `opencode_work_ask` for follow-up questions in that same task.
3. Call `opencode_work_end` when the task is finished or the context is stale.
4. If you must reuse a `work_id` after a stale start, call
   `opencode_work_start` again with `on_exists="resume"` to continue
   the existing OpenCode session, or `on_exists="replace"` to start
   fresh under the same identifier.

Use a new `work_id` for a different bug, feature, review, or design thread.
Prefer `opencode_ask` for isolated one-off questions.
