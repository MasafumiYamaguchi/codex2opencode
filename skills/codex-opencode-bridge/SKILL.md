---
name: codex-opencode-bridge
description: Use when Codex should consult OpenCode through the local codex2opencode MCP server for coding work, especially second opinions, reviews, debugging hypotheses, design alternatives, or task-scoped collaboration. Prefer this skill when OpenCode context should persist for one bug, feature, review, or design task via one-work-one-session tools.
---

# Codex OpenCode Bridge

Use OpenCode as a task-scoped second opinion, not as a replacement for Codex's own implementation work.

## Workflow

1. Decide whether OpenCode should help.
   - Use it for reviews, alternative designs, debugging hypotheses, risk checks, and "argue with this plan" prompts.
   - Avoid it for tiny syntax fixes or facts Codex can verify directly from local files.

2. Choose the session mode.
   - For a one-off question, call `opencode_ask`.
   - For a real unit of work, start with `opencode_work_start`.
   - For follow-ups in the same unit of work, call `opencode_work_ask`.
   - When the unit of work is done or the context is stale, call `opencode_work_end`.

3. Keep one OpenCode session per work unit.
    - A work unit is one bug, feature, review, refactor, or design thread.
    - Do not reuse an old work session for an unrelated task.
    - Use stable, short `work_id` values such as `fix-auth-timeout`, `review-sidebar-ui`, or `design-session-flow`.

4. Send compact, role-specific prompts.
    - Include the goal, relevant files or observations, and the kind of response wanted.
    - Ask OpenCode for judgment, objections, missing tests, or competing approaches.
    - Do not paste large code blocks unless they are essential; point to file paths and summarize findings when possible.
    - Pass `cwd` for the target repository when using this bridge outside the codex2opencode repo. If the user has set `CODEX2OPENCODE_DEFAULT_CWD` in the MCP server env, you can usually omit it.

5. Treat OpenCode output as advisory.
    - Verify claims against the worktree before editing.
    - Prefer Codex's current inspected state over stale OpenCode session memory.
    - If OpenCode's answer conflicts with current files, re-check locally and continue with evidence.

## Tool Use

Use `opencode_work_start` at the first meaningful consultation for a task:

```text
work_id: short task slug
prompt: what OpenCode should evaluate
cwd: target repository path, when outside codex2opencode
       (omit if CODEX2OPENCODE_DEFAULT_CWD is set on the server)
on_exists: error | resume | replace (default: error)
```

`on_exists` controls what happens when the same `work_id` is reused:

- `error` (default): the call fails fast with `work_id already exists`.
  Use this when you want to detect accidental reuse.
- `resume`: the new `prompt` is sent as a follow-up to the existing
  OpenCode session, reusing its stored `cwd`, `agent`, `model`, and
  `attach_url`. Prefer this when the old context is still useful.
- `replace`: the previous local reference is dropped and a fresh
  OpenCode session is started under the same `work_id`. Use this when
  the old context is stale or wrong and you want a clean slate under
  the same name. The previous OpenCode session itself is not deleted.

When `work_id` is new, `on_exists` is ignored.

Use `opencode_work_ask` for follow-ups:

```text
work_id: omit to continue the active work, or specify explicitly
prompt: focused continuation question
```

Use `opencode_work_list` when unsure which work session is active.

The response includes a `summaries` list sorted by `last_used_at` descending
(most recent first), with `work_id`, `session_id`, `cwd`, `agent`, `model`,
`created_at`, `last_used_at`, and `turn_count` for each session. Use it to
find an active session and to decide whether a work unit is stale enough
to end.

Use `opencode_work_end` after the task is complete, abandoned, or context has become misleading.

If a `cwd` is rejected, check that the repository is trusted in Codex config, is included in `CODEX2OPENCODE_ALLOWED_ROOTS`, or matches `CODEX2OPENCODE_DEFAULT_CWD`.

## Prompt Patterns

For reviews:

```text
Review this approach for hidden risks and missing tests. Focus on behavioral regressions, not style.
```

For debugging:

```text
Given these symptoms and files, propose the top hypotheses and the fastest checks to distinguish them.
```

For design:

```text
Compare two implementation approaches for this repo and point out failure modes I should verify locally.
```

For counterargument:

```text
Act as a skeptical reviewer. What could be wrong with this plan, and what evidence should I gather?
```
