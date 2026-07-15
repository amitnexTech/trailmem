# trailmem

**Persistent, local-first graph memory for AI coding agents.**

Trailmem gives agents durable cross-session memory without provider lock-in: a local SQLite knowledge graph, typed relationships, explicit knowledge evolution, and token-disciplined briefings. It is designed for multiple local agents—Claude, Kiro, Codex, OpenCode, Kilo, and Gemini—to share useful project knowledge without silently creating junk memories.

## Project Status

**Design specification phase.** Trailmem has no implementation yet. The documents under [`docs/`](docs/) are the source of truth for its architecture and behavioral contracts. Implementation begins only after remaining design decisions are explicitly approved.

## Principles

- Local-first storage; no cloud dependency.
- Linked knowledge, not an unstructured memory list.
- Mandatory titles, explicit agent attribution, project-aware scope, and no orphan memories.
- Archive/supersede history instead of destructive overwrites.
- Stdio MCP in v1; no daemon or HTTP MCP service.
- Quiet, accessible local dashboard; no disruptive polling redraws.
- No telemetry, no phone-home, no usage-tracking file. The server writes only what the user needs (e.g. a local `hooks.log` diagnostic); it never emits generic analytics — a deliberate anti-goal, not an oversight.

## Documentation

Start with [the Trailmem specification index](docs/index.md). It links the schema, welcome lifecycle, duplicate policy, evolution rules, CLI/MCP surfaces, hooks, manual seeding playbook, and dashboard contract.

## License

MIT (planned).
