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

5. Treat OpenCode output as advisory.
   - Verify claims against the worktree before editing.
   - Prefer Codex's current inspected state over stale OpenCode session memory.
   - If OpenCode's answer conflicts with current files, re-check locally and continue with evidence.

## Tool Use

Use `opencode_work_start` at the first meaningful consultation for a task:

```text
work_id: short task slug
prompt: what OpenCode should evaluate
cwd: optional repo-relative directory
```

Use `opencode_work_ask` for follow-ups:

```text
work_id: omit to continue the active work, or specify explicitly
prompt: focused continuation question
```

Use `opencode_work_list` when unsure which work session is active.

Use `opencode_work_end` after the task is complete, abandoned, or context has become misleading.

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
