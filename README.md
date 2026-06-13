# codex2opencode

Personal FastMCP bridge for asking OpenCode from Codex.

## Tools

This exposes a local stdio MCP server with these tools:

- `opencode_ask`: runs `opencode run` with a focused prompt.
- `opencode_status`: checks whether the OpenCode CLI is available.
- `opencode_work_start`: starts a named unit of work and remembers the OpenCode session ID.
- `opencode_work_ask`: continues the current or specified unit of work with `--session`.
- `opencode_work_list`: lists remembered work sessions.
- `opencode_work_cleanup`: marks or removes stale work sessions by `last_used_at`.
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

## Prompt Modes

`opencode_ask`, `opencode_work_start`, and `opencode_work_ask` all accept
a `mode` argument that prepends a small role-specific prompt prefix so the
caller does not have to repeat common framing. Valid values are:

- `none` (default): no prefix, the prompt is sent as-is.
- `review`: ask for a careful code review focused on risks, regressions,
  and missing tests.
- `debug`: ask for ranked hypotheses and the fastest checks that would
  distinguish them.
- `design`: ask for a comparison of approaches and the trade-offs to
  verify locally.
- `skeptic`: ask for an argument against the plan and the evidence that
  would change the answer.
- `test-plan`: ask for concrete test cases including edge cases and
  regression risks.

The mode is applied to the prompt before it is sent to OpenCode. The
chosen mode is also recorded in each invocation log entry. When
`opencode_work_start` is used, the mode is not stored on the work
session; only the prompt the user actually wrote is remembered.

## Compact Responses

All four main tools accept a `compact: bool = False` argument. When
`compact=True`, the response is limited to the fields most callers
triage on:

- `ok`
- `work_id` (when applicable)
- `session_id` (when applicable)
- `cwd`
- `text`
- `resumed` / `replaced` (when applicable)
- `agent_override` / `model_override` (when a follow-up used an override)
- `error` (when set)

Full `stdout`, `stderr`, `args`, `parsed` JSON, and event lists are
omitted. Use the full response (the default) when you need them.

## Cleanup Of Stale Sessions

`opencode_work_cleanup` marks or removes remembered work sessions whose
`last_used_at` (or, as a fallback, `created_at`) is older than a
threshold. The active work session is skipped unless `include_active=True`
is passed.

Arguments:

- `older_than_seconds` (default 7 days): staleness threshold. Entries
  without any timestamp metadata are also treated as stale.
- `include_active` (default `False`): include the active `work_id` in
  the cleanup set.
- `mark_only` (default `False`): keep the entry but set `stale: True`
  and `stale_marked_at` to the current UTC time. Useful for review
  before deletion.
- `dry_run` (default `False`): report what would be affected without
  writing state.

The response lists the affected `work_id` values, plus `marked`,
`removed`, `count`, and the ISO timestamp used as the threshold.
`opencode_work_list` surfaces the `stale` flag on each summary so
marked sessions are easy to spot.

## Per-Call Model Or Agent Override

`opencode_work_ask` accepts `agent` and `model` overrides. When either
is provided, it is used for just that single follow-up call; the stored
`agent` and `model` on the work session are not changed. This lets a
caller keep the original setup (for example, a `reviewer` agent on a
fast model) while occasionally poking the same session with a
different agent or model (for example, to sanity-check a hypothesis
with a stronger model). A blank or whitespace-only override falls
back to the stored value.

When an override is applied, the response includes `agent_override` or
`model_override` so callers can confirm which value was used.

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
- `mode`: prompt mode (see [Prompt Modes](#prompt-modes)).
- `compact`: when `True`, return only the small set of triage fields
  (see [Compact Responses](#compact-responses)).

`opencode_work_start` accepts everything above that applies, plus:

- `work_id`: stable, short identifier for the unit of work.
- `on_exists`: `"error"` (default), `"resume"`, or `"replace"`. Controls
  what to do when `work_id` already exists in local state:
  - `"error"` raises `ValueError` (the original strict behavior).
  - `"resume"` continues the existing OpenCode session with the new
    `prompt` and reuses the stored `cwd`, `agent`, `model`, and
    `attach_url`. The response includes `resumed: True`.
  - `"replace"` forgets the previous local reference and starts a
    fresh OpenCode session under the same `work_id`. The previous
    OpenCode session itself is left untouched. The response includes
    `replaced: True` on success.
  When `work_id` does not exist yet, `on_exists` is ignored.
- `mode`: prompt mode (see [Prompt Modes](#prompt-modes)).
- `compact`: when `True`, return only the small set of triage fields
  (see [Compact Responses](#compact-responses)).

`opencode_work_ask` accepts:

- `prompt`: focused continuation question.
- `work_id`: optional; defaults to the active work session.
- `timeout_sec`: timeout from 1 to 900 seconds.
- `agent`: optional one-call override (see
  [Per-Call Model Or Agent Override](#per-call-model-or-agent-override)).
  Blank values fall back to the stored agent.
- `model`: optional one-call override (see
  [Per-Call Model Or Agent Override](#per-call-model-or-agent-override)).
  Blank values fall back to the stored model.
- `mode`: prompt mode (see [Prompt Modes](#prompt-modes)).
- `compact`: when `True`, return only the small set of triage fields
  (see [Compact Responses](#compact-responses)).

`opencode_work_cleanup` accepts:

- `older_than_seconds`: staleness threshold; default 7 days.
- `include_active`: include the active `work_id`; default `False`.
- `mark_only`: keep but flag entries with `stale: True`; default `False`.
- `dry_run`: report what would change without writing state; default
  `False`.

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
