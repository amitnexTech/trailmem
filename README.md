# trailmem

**Persistent, local-first graph memory for AI coding agents.**

Trailmem gives agents durable cross-session memory without provider lock-in: a local SQLite knowledge graph, typed relationships, explicit knowledge evolution, and token-disciplined briefings. It is designed for multiple local agents—Claude, Kiro, Codex, OpenCode, Kilo, and Gemini—to share useful project knowledge without silently creating junk memories.

## Quick start

```bash
pip install trailmem
trailmem setup          # creates ~/.trailmem/, inits DB, downloads the default embedding model
trailmem doctor         # health check

# Register the MCP server with your agent host, e.g. Claude Code:
claude mcp add trailmem -- trailmem-mcp
```

The agent then gets six tools: `trailmem_welcome` (once-per-session briefing), `trailmem_store`, `trailmem_query`, `trailmem_show`, `trailmem_edit`, `trailmem_link`. Everything is also available to humans via the `trailmem` CLI (`store`, `query`, `show`, `list`, `stats`, `link`, `archive`, ...).

Try it from the CLI (note: `content` is positional; `--agent user` for your own notes):

```bash
trailmem store --title "First note" --type lesson --agent user "Something worth remembering."
trailmem query "what did I note earlier"
trailmem list
trailmem help                # or: trailmem <command> --help
```

## Why

- **Local-first.** One SQLite file (`~/.trailmem/trailmem.db`), WAL mode, no cloud, no daemon. Embeddings run locally via ONNX (default: bge-small-en-v1.5, user-swappable with `trailmem model use`).
- **A graph, not a list.** Typed edges (`related`, `supersedes`, `evolves`, `contradicts`, `derived_from`), orphan warnings at store time, supersede chains instead of destructive overwrites.
- **Token discipline.** Context is injected exactly once per session (welcome, ~600–800 tokens). No per-turn injection, ever. Repeat welcomes return a short form.
- **No junk memories.** 4-band duplicate detection (exact hash reject → >0.92 block → 0.85–0.92 warn → accept), mandatory titles, hard-reject on unattributed stores, no auto-store lifecycle hooks.
- **No telemetry.** The server writes only what the user needs (e.g. a local `hooks.log` diagnostic); it never emits analytics — a deliberate anti-goal, not an oversight.

## Status

Core implemented and tested (schema, store/dedup, query/show, welcome, MCP server, CLI, hooks, model management). Not yet published to PyPI. The design contract lives in [`docs/`](docs/index.md) — schema, welcome lifecycle, duplicate policy, evolution rules, CLI/MCP surfaces, hooks, seeding playbook, and the (deferred) dashboard contract.

## License

MIT
