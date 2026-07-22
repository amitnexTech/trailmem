# trailmem — Agent Guide

trailmem is a local-first, graph-linked persistent-memory MCP server for AI coding agents (Python, open-source, MIT). This repo is in the **design → implementation** transition: the full spec is locked (Q1–Q16); code is being written now.

This file is the single source of truth for any coding agent working here. `CLAUDE.md` just imports it (`@AGENTS.md`); other agents (Codex, Kiro, Kilo, OpenCode, Cursor, Gemini) read this file directly.

## Session boot sequence — do this first

1. **Read the auto-loaded build-log memory** (`MEMORY.md` for Claude; the trailmem MCP briefing block if a pinned/briefing is already injected into context). Do NOT call `trailmem_welcome` if a briefing is already present — it just wastes tokens.
2. **Read the relevant `docs/` page** for the task (via the open-knowledge MCP, see below) before touching code.
3. **Before non-trivial work**, query trailmem for prior decisions/lessons instead of re-deriving state or re-asking: `trailmem_query("<topic>")`. Check the pinned constraints first — they carry standing rules.
4. Only then start work. Not doing steps 1–3 is a known cause of re-deriving locked design and token burn.

## Tool usage rules — pick the right tool for the question

| Question type | Use this | NOT this |
|---|---|---|
| Codebase / file relationships / "how does X work" | **graphify** (`graphify_query_graph`, `graphify_shortest_path`, `graphify_get_node`) — graph lives at `graphify-out/graph.json` | raw `grep`/`Glob`/source reading from scratch |
| Design spec / `docs/*.md` | **open-knowledge MCP** (`mcp__open-knowledge__search`, `mcp__open-knowledge__exec`) — start at `docs/index.md` | native Read/Grep/Glob on `docs/*.md` |
| Past decisions / lessons / cross-session context | **trailmem MCP** (`trailmem_query`, `trailmem_show`) | re-asking the user, or guessing |
| Storing a decision/lesson/task | **trailmem MCP** `trailmem_store` (English, project-scoped path `/home/amit/trailmem`, link every memory) | only file-notes that never reach trailmem |

### graphify — code knowledge graph
- For code-structure questions, run `graphify_query_graph` first. It returns a scoped subgraph, usually far smaller than raw grep or `GRAPH_REPORT.md`.
- Use `graphify_shortest_path "<A>" "<B>"` for relationships, `graphify_get_node "<label>"` for a focused concept.
- Dirty `graphify-out/` files after hooks/incremental updates are expected — not a reason to skip graphify. Only skip if the task is about stale/incorrect graph output itself, or the user says not to.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

### trailmem — persistent memory
- This project's continuity is **file-based primary** (`MEMORY.md` + `docs/`) — see "Memory & continuity" below. trailmem MCP is the cross-session persistent store that mirrors it.
- **Standing rule (global #33):** whenever file-based memory is created or updated, sync the matching trailmem record in the SAME pass. Query for an existing matching record first and edit it instead of duplicating.
- Do NOT call `trailmem_welcome` if a briefing block is already in context (it auto-injects when pinned rules exist).
- Content language: English only. Project-scoped records use the full absolute path `/home/amit/trailmem`.
- Task done? Close it immediately: `trailmem_edit(ref='#N', status='completed', archive_reason='<what happened + evidence>', link_to=<completion memory>)`. Use `cancelled` for dropped work; reserve `archived` for wrong/outdated info only.
- `user_preference` is a singleton (one active global record, ever) — never store a new one; merge into the existing record with `trailmem_edit`. A new store is `blocked_singleton` and `force=true` does not bypass it.

### open-knowledge — the `docs/` spec
- `docs/` is OpenKnowledge-managed (CRDT-backed) and interlinked; `docs/schema.md` is the hub. Mandatory for reading/writing the spec.
- Read/search via `mcp__open-knowledge__exec` / `mcp__open-knowledge__search`, never native Read/Grep/Glob on `docs/*.md`.

## Skill mapping — use the right skill for the task

- `open-knowledge` — mandatory for reading/writing `docs/`.
- `ponytail` — minimal-code discipline when WRITING implementation code (no bloat, no premature abstraction). Do NOT invoke for brainstorming/discussion/review.
- `graphify` — code-structure questions and after-code graph refresh.
- `trailmem` — when saving/recalling persistent memory or a trailmem tool call fails / params are unclear.

## Source of truth — read before any work

- **`docs/`** — the complete locked design spec: schema, welcome, dedup, evolution, mcp, cli, hooks, migration, dashboard. This is the contract: implement to it, don't re-derive it.
- **Build log** — session-to-session progress and "what's next" lives in this project's file-memory (auto-loaded each session as `MEMORY.md`). Read it first to catch up.

## Memory & continuity — file-based, NOT OMEGA

This project deliberately does **not** use the OMEGA MCP server for its own workflow. OMEGA injects a welcome/protocol/tool-dump into context every session (the exact bloat trailmem is being built to replace). Continuity here is file-based:

- **Session start:** read the auto-loaded build-log memory + the relevant `docs/` page. Do NOT call `omega_welcome`/`omega_protocol`.
- **After any session with real progress:** update (a) the build-log memory — what changed + the immediate next step, and (b) the relevant `docs/` page if the design shifted during implementation. Keep both lean. Then sync the matching trailmem record (global rule #33).
- OMEGA has one backup pointer record for this project, but it is optional — file-memory + `docs/` are primary.

## Implementation guardrails (from the spec — do not violate)

- Every DB connection, first lines: `PRAGMA foreign_keys = ON; PRAGMA busy_timeout = 3000;`
- Virtual tables (`memories_vec`, `memories_fts`) do NOT cascade — delete from all three tables explicitly.
- Vector dims are config-driven `float[N]`, never hardcoded 384 (model is user-configurable).
- `agent_type` undetectable → hard reject; never store an unattributed / "unknown" memory (the original OMEGA bug).
- Boundary query excludes the current `session_id`.
- Stored memory content is English-only (soft-warn + pinned constraint, not hard reject).
- No cache layer — indexes + WAL + in-RAM model are enough at this scale (YAGNI).

## Build / test

- Confirm `trailmem` name availability on PyPI before the first publish.
- After code changes, run `graphify update .` to refresh the code graph (output in `graphify-out/`, gitignored).
- Author identity is a placeholder email until open-sourcing — swap to real before publishing.
