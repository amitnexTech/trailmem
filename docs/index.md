# trailmem — Specification Index

Trailmem is a local-first, graph-linked persistent memory system for AI coding agents. These documents define the design contract before implementation.

**Status:** REFERENCE

## Core Specifications

- [[schema]] — SQLite schema, connection rules, sessions, search indexes, and model configuration.
- [[welcome]] — Cross-session briefing, boundary handling, and anti-bloat behavior.
- [[dedup]] — Exact and semantic duplicate policy, including FTS-only degradation.
- [[evolution]] — Minor edits, archives, supersession, and knowledge-history rules.
- [[cli]] — Human-facing command-line surface.
- [[mcp]] — Six stdio MCP tools, response boundaries, and concurrency rules.
- [[hooks]] — Minimal SessionStart/Stop lifecycle integration.
- [[host-integration]] — Add a new agent host through one auto-discovered adapter module.
- [[migration]] — One-time human-reviewed quality seed; no vendor importer.
- [[dashboard]] — Quiet, local-first graph dashboard design contract.

## Threat model

Trailmem is single-user and local-first: the trust boundary is the local OS
account. The MCP server speaks stdio only (never a network socket; the
dashboard binds loopback), the DB is plain SQLite under `~/.trailmem/` with
the account's file permissions, and nothing leaves the machine. Anyone or
anything running as that account (including every integrated agent) can read
and write all memories — protecting memories from a compromised local account
or from one agent snooping on another's is explicitly out of scope.

## Status

The design is locked (Q1–Q16) and **implemented**: schema, store/dedup, query/show, welcome, MCP server, CLI, hooks, model management, host integration (`trailmem integrate`), and the loopback dashboard (built, audited, hardened) all ship and are verified against these documents. The specs remain the contract — code follows them; when implementation reality diverges, the spec page is updated in the same change.
