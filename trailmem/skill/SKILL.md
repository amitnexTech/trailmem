---
name: trailmem
description: Use when saving or recalling persistent memory via trailmem MCP tools (trailmem_store, trailmem_query, trailmem_welcome, ...), when a trailmem tool call fails or is rejected, or when unsure how to fill its parameters (project scoping, agent_type, event_type, dedup, linking, archiving).
---

# trailmem — tool usage

Local-first, graph-linked persistent memory (MCP server, SQLite). Six tools.
This skill is the usage reference — never read trailmem's source code to figure
out a parameter; everything an agent needs is here or in the tool docstrings.

## Session workflow

1. **Start:** if a trailmem briefing (pinned rules / recent activity block) is
   already in your context, do NOT call `trailmem_welcome` — a hook injected it.
   Otherwise call it once.
2. **During:** query before assuming (`trailmem_query`), read in full with
   `trailmem_show` before editing.
3. **End:** store this session's durable decisions/lessons/tasks with
   `trailmem_store`. Never store filler or session noise.

## Parameter rules (the ones agents get wrong)

- **project** — OMIT it. The server auto-fills the absolute path from its
  working directory. Pass `"global"` only for cross-project memories (tool
  preferences, workflow rules). Pass an absolute path only to target a
  *different* project. Bare names like `"myproject"` are rejected.
- **agent_type** — OMIT it. Attribution comes from `TRAILMEM_AGENT_TYPE`
  pinned in the host's MCP config entry. Pass it explicitly only if store
  rejects with "agent_type could not be determined".
- **code_files / doc_files** — fill BOTH when relevant, comma-separated
  paths: `code_files` = source/config files the memory touches, `doc_files` =
  docs/spec pages. Don't record only the docs and skip the code files.
- **content** — English only. 50+ chars. Detailed prose beats terse bullets.
- **title** — 3–60 chars.
- **event_type** — required on store:
  - `decision` — rules, tool choices, structure, enforced behavior
  - `lesson` — bugs/mistakes learned (include root cause)
  - `error_pattern` — things that failed and how they fail
  - `task` — pending work
  - `constraint` — hard rules (auto-surfaced in every welcome — use sparingly)
  - `user_preference` — personal choices only
  - `session_summary`, `memory` — summaries / plain facts

## Dedup responses are not errors

`trailmem_store` replies like `Rejected: exact duplicate of #12 ...` or
`Blocked: 87% similar to #12 ...` are SUCCESS responses with a next action:
update the existing memory via `trailmem_edit(ref='#12')` instead of retrying.
Use `force=true` only when the new memory is genuinely distinct.

## Linking (no orphans)

Every memory should link to at least one related memory. Edge types:
`related`, `derived_from`, `supersedes`, `contradicts`, `evolves`.

- On store: `link_to='#12'` (+ optional `edge_type`), or `supersedes='#12'`
  to archive the old one atomically.
- Later: `trailmem_link(action='add', source='#new', target='#old',
  edge_type='related')`. Edge ids for removal come from `trailmem_show`.

## Editing & archiving

- `trailmem_edit(ref='#12', content=...)` — content/title/type/pin updates;
  embedding and search index refresh automatically.
- Archive: `trailmem_edit(ref='#12', status='archived', archive_reason=...)`
  — reason must be ≥20 chars AND the memory must have at least one edge.

## Common failures

| Symptom | Fix |
|---|---|
| "agent_type could not be determined" | Host MCP config entry is missing the `TRAILMEM_AGENT_TYPE` env pin — re-run `trailmem integrate`, or pass `agent_type` explicitly |
| "project must be an absolute path or 'global'" | You passed a bare name — omit `project` or pass the full path |
| Store rejected/blocked as duplicate | Not an error — edit the existing `#id` instead |
| Archive refused | Add an edge first, or lengthen `archive_reason` to ≥20 chars |
| Welcome shows short form | Already called this session — that is the anti-bloat guard, not a failure |
