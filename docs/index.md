# trailmem — Specification Index

Trailmem is a local-first, graph-linked persistent memory system for AI coding agents. These documents define the design contract before implementation.

## Core Specifications

- [[schema]] — SQLite schema, connection rules, sessions, search indexes, and model configuration.
- [[welcome]] — Cross-session briefing, boundary handling, and anti-bloat behavior.
- [[dedup]] — Exact and semantic duplicate policy, including FTS-only degradation.
- [[evolution]] — Minor edits, archives, supersession, and knowledge-history rules.
- [[cli]] — Human-facing command-line surface.
- [[mcp]] — Six stdio MCP tools, response boundaries, and concurrency rules.
- [[hooks]] — Minimal SessionStart/Stop lifecycle integration.
- [[migration]] — One-time human-reviewed quality seed; no vendor importer.
- [[dashboard]] — Quiet, local-first graph dashboard design contract.

## Status

The design is locked (Q1–Q16) and **implemented**: schema, store/dedup, query/show, welcome, MCP server, CLI, hooks, model management, host integration (`trailmem integrate`), and the loopback dashboard (built, audited, hardened) all ship and are verified against these documents. The specs remain the contract — code follows them; when implementation reality diverges, the spec page is updated in the same change.
