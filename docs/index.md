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

All documents are **design specifications**, not shipped behavior. The dashboard document deliberately identifies decisions that need explicit approval before implementation.
