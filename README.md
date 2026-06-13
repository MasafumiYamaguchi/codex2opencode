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

## One Work, One Session

Use this workflow when Codex should keep OpenCode's memory warm for a task:

1. Call `opencode_work_start` with a stable `work_id` and the first prompt.
2. Call `opencode_work_ask` for follow-up questions in that same task.
3. Call `opencode_work_end` when the task is finished or the context is stale.

Use a new `work_id` for a different bug, feature, review, or design thread.
Prefer `opencode_ask` for isolated one-off questions.
