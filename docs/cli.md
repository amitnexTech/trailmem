# trailmem — CLI Reference

Complete reference for the `trailmem` command-line interface: write/read/list/admin/model subcommands, ref resolution, output formats, environment variables, and exit codes.

**Status:** REFERENCE

## Overview

`trailmem` is the command-line interface for managing agent memory. All operations available via MCP tools are also available via CLI.

**Installation:** `uv tool install trailmem` (recommended: on PATH, no pre-installed Python needed; pipx or a venv pip install also work)
**Binary:** `trailmem`
**Help:** `trailmem` (no args), `trailmem help`, or `trailmem --help` all print top-level help with usage examples and exit 0; `trailmem <command> --help` shows per-command flags.

---

## Commands

### WRITE Operations

```bash
# Store a new memory
trailmem store "content here" \
    --title "Short Title" \
    --type decision \
    --agent kiro \
    --pin \
    --link-to mem-abc123 \
    --edge-type related \
    --work-type code-written \
    --source "file:docs/apps/jarvis-play.md"
# Also: --supersedes <ref> --archive-reason "why (min 20 chars)" for one-call supersede,
#       --code-files "a.py,b.py" --doc-files "docs/x.md", --force to bypass the >0.92 near-dup block (exit 4)

# Edit existing memory
trailmem edit <ref> --content "updated content"
trailmem edit <ref> --title "new title"
trailmem edit <ref> --type lesson
trailmem edit <ref> --pin            # or --no-pin
trailmem edit <ref> --status archived --reason "why (min 20 chars)" --link-to <ref>
trailmem edit <ref> --status completed --reason "what happened + evidence"   # task done (also: cancelled)
trailmem edit <ref> --project /abs/path   # rescope a misfiled memory (or 'global'); content untouched

# Archive (primary way to "remove" — preserves knowledge trail)
trailmem archive <ref> --reason "replaced by QTcpSocket approach" --link-to <ref>

# Link two memories
trailmem link <source-ref> <target-ref> --type related --reason "both about aria2"

# Remove a link
trailmem unlink <edge-id>

# Pin/unpin
trailmem pin <ref>
trailmem unpin <ref>
```

### READ Operations

```bash
# Show single memory (full detail + edges)
trailmem show <ref>
# Output: #id [node_id] title, type, agent, status, content, all edges

# Search memories
trailmem query "wayfire compositor"
trailmem query "aria2" --type lesson
trailmem query "build" --agent claude
trailmem query "aria2" --limit 10 --format json   # default limit 5, text output

# Session briefing (same as MCP welcome)
trailmem welcome

# Check near-duplicates before storing
trailmem similar "content to check"
# Shows: band (exact/0.92+/0.85+/low) + matching memory
```

### LIST Operations (filterable)

```bash
trailmem list                     # all active memories
trailmem list --recent            # last 10, newest first
trailmem list --orphans           # zero-edge memories
trailmem list --pinned            # pinned + constraints
trailmem list --tasks             # open tasks
trailmem list --timeline          # grouped by day
trailmem list --archived          # archived/superseded
trailmem list --by-agent claude   # filter by agent
trailmem list --project /path     # filter by project
trailmem list --global            # only global (project=NULL)
```

### ADMIN Operations

```bash
# Statistics
trailmem stats
# Output: memory count, edge count, orphans, DB size, per-type breakdown

# Maintenance (DRY-RUN by default — report only)
trailmem maintain
# Output: what would be cleaned (old sessions, orphan report)
# Does NOT delete/archive anything without --apply

trailmem maintain --apply
# DESTRUCTIVE: purge sessions >90 days, report orphans
# Confirmation prompt before execution
# NEVER auto-archives/deletes memories

# Backup & Restore
trailmem export [output.json]      # full DB dump
trailmem import <file.json> --merge    # add without overwriting existing
trailmem import <file.json> --replace  # full overwrite (DOUBLE confirmation!)

# Web Dashboard
trailmem dashboard
# Starts web UI at http://127.0.0.1:3800 (loopback-only, Ctrl-C to stop)
# Flags: --port N | --project <all|global|path> (default 'all' = global + every project; runtime scope switcher + per-project node colors in the UI) | --agent <default attribution for UI-created memories>

# Setup (first-time) — identical on Windows / macOS / Linux (pure-Python package)
trailmem setup
# Creates ~/.trailmem/, downloads the default embedding model, prints MCP registration hint.
# Note: some systems use `pip3` or `python -m pip` instead of `pip`.

# Integrate with agent hosts (auto-detect, permission-gated)
trailmem integrate
# Detects 9 hosts (Claude Code via `claude` on PATH; Codex / Kiro / Kilo / OpenCode /
# Antigravity / Zed / Cursor / Windsurf via their config paths). Host knowledge is
# MODULAR: trailmem/hosts/ has one module per host exposing HOST with paired
# install/remove artifacts — integrate and uninstall iterate the SAME registry, so the
# two cannot drift; the registry auto-discovers modules, so a new host = one file.
# WRITE POLICY (narrow-write / wide-detect): after the y/N prompt, configs are
# auto-written ONLY for hosts whose format is verified against the live binary —
# Claude Code (via its own `claude mcp add`), Codex (TOML), Kiro, Kilo, OpenCode,
# Antigravity (all live-verified 2026-07-23; the pre-verification corruption is
# history; Antigravity has NO `agy mcp` CLI — the file is the only path). Every OTHER
# detected host gets the exact manual entry PRINTED instead of having its config
# edited (hand-written entries corrupted Kilo and OpenCode configs before; flip a
# host module's write flag once its format is verified). No silent changes.
# (Manual fallback: `claude mcp add trailmem
# -e TRAILMEM_AGENT_TYPE=claude -- <python> -u -m trailmem.mcp_server`.)
# Server launch is ALWAYS `<python> -u -m trailmem.mcp_server` — the generated
# `trailmem-mcp` script was removed in 0.1.7: Windows Smart App Control blocks unsigned
# per-install launcher .exes, silently killing host-spawned servers. integrate upgrades
# old trailmem-mcp entries to the python -m shape in place (env pins preserved).
# EVERY entry pins TRAILMEM_AGENT_TYPE=<host> in the entry's env map — verified live:
# Codex and Kiro spawn MCP servers with a CLEAN env, while Kilo passes its FULL parent
# env down (including stale vars from before a config edit — a Kilo env change needs a
# session restart to reach the server). Either way the config-entry pin is the only
# reliable attribution path. Env-key name
# is host-specific and schema-verified: Kilo + OpenCode use `environment`, everything else
# `env` (Codex: TOML inline table; Claude Code: `claude mcp add -e`). Re-running integrate
# UPGRADES an existing entry that lacks the env map instead of skipping it. Codex paths all
# follow $CODEX_HOME (official manual; default ~/.codex — self-report 0.145.0). Codex also
# gets $CODEX_HOME/prompts/trailmem-save.md → /prompts:trailmem-save (no MCP-prompt surface
# observed) AND two hooks merged into $CODEX_HOME/hooks.json: SessionStart for one welcome,
# plus a TrailMem-only PreToolUse adapter that silently carries canonical
# session_context into MCP arguments. Codex Stop is turn-scoped — never used. See [[hooks]].
# Antigravity gets one named hook group "trailmem" in
# ~/.gemini/config/hooks.json with two handlers: PreInvocation →
# `trailmem hook pre-invocation` (fires before EVERY model call, so the
# command dedups per conversationId — marker in ~/.trailmem/welcomed/ —
# briefing injected via injectSteps once per conversation, bare {} after),
# and PreToolUse (matcher call_mcp_tool) → `trailmem hook tool-context`,
# which rewrites ONLY trailmem MCP calls: agy's `overwrite` shallow-merge
# gets the full Arguments echoed back with session_context added; foreign
# servers get a bare {}. Welcome stays stateless until the transport is
# live-proven. Restart agy after install. See [[hooks]].
# Kiro gets a SessionStart hook file at <cwd>/.kiro/hooks/ — WORKSPACE-scoped,
# because Kiro never executes user-level ~/.kiro/hooks/ (verified live
# 2026-07-23); run integrate once per Kiro workspace. Installs and uninstall
# also delete the dead ~/.kiro/hooks/trailmem-session-start.json that ≤0.1.8
# wrote. Kiro's hook payload carries an always-empty session_id → stateless.
# Claude Code and Antigravity also get a statusline: if the host's settings.json
# (~/.claude/settings.json / ~/.gemini/antigravity-cli/settings.json) has NO
# statusLine, integrate writes `<python> -m trailmem statusline --agent <slug>`
# there; an existing user statusline is NEVER overwritten (the exact command to
# chain into it is printed instead). --agent is required because the statusline
# process is spawned outside the MCP entry, so the TRAILMEM_AGENT_TYPE env pin
# never reaches it. Uninstall removes the statusLine only when it is trailmem's own.
# ANY other MCP agent works manually: stdio transport, command `<python> -u -m
# trailmem.mcp_server`, and TRAILMEM_AGENT_TYPE=<lowercase-slug>. Set
# TRAILMEM_SESSION_ID when the host exposes a stable conversation ID; otherwise
# CRUD works in stateless mode and no boundary/save-status claims are made.
# README has the generic guide ("Any other MCP agent") with the common JSON shape;
# the right <python> is printed by `python -c "import sys; print(sys.executable)"` from
# the environment trailmem is installed into.

# Health check
trailmem doctor
# Verifies: installed version, home + config presence, DB tables, sqlite-vec
# extension, embedding model. Then a host-capability section: every detected
# host's artifacts (MCP registration, skill, hooks, statusline) with config
# drift flagged — a STALE trailmem-mcp launcher or a missing TRAILMEM_AGENT_TYPE
# pin says "run `trailmem integrate`". Finally lists running MCP server
# processes (POSIX `ps`; skipped on Windows) and flags any started BEFORE this
# install — old servers keep writing with old code until their host restarts.

# Self-update (no manual reinstall)
trailmem update
# Checks PyPI for a newer release; picks the upgrade command from HOW this copy
# was installed (uv tool → `uv tool install trailmem@latest --force` — a once-pinned
# tool makes bare `uv tool upgrade` a no-op; pipx → `pipx upgrade`; else pip -U).
# Editable/dev installs are refused (update via git). After upgrading it reminds
# the user to run `trailmem integrate` (refreshes host configs — old entries are
# upgraded in place when the launch shape changed, e.g. pre-0.1.7 trailmem-mcp)
# and then restart agents — schema migrations run on first new-code start and
# old servers must not keep writing.

# Uninstall (surgical reversal of integrate; memories KEPT by default)
trailmem uninstall
# After one y/N prompt, SURGICALLY removes only trailmem's own artifacts and leaves
# everything else in each config intact — it walks the SAME hosts/ registry as
# integrate (every artifact pairs install with remove), covering ALL hosts including
# ones this release no longer auto-writes (older releases did): the `trailmem` entry
# in every JSON host config
# (Kiro/Kilo/OpenCode/Antigravity/Zed/Cursor/Windsurf), the Codex
# `[mcp_servers.trailmem]` TOML table, the Claude Code registration (via `claude mcp
# remove --scope user`), the usage skill at <skills>/trailmem/SKILL.md
# (claude/codex/kilo/opencode user-level; antigravity per-workspace at
# <cwd>/.agents/skills/), ~/.claude/commands/tm-save.md,
# ~/.config/kilo/command/tm-save.md, ~/.config/opencode/commands/tm-save.md,
# ~/.codex/prompts/trailmem-save.md, and both TrailMem hook entries in
# ~/.codex/hooks.json (foreign hooks untouched). The one-time .bak-trailmem backups are NOT
# restored (the user may have edited configs since integrate); unparseable JSONC
# configs are never rewritten — a manual-removal instruction is printed instead.
# ~/.trailmem (ALL memories) is KEPT by default: most uninstalls are temporary
# (reinstall/upgrade/broken install) and reinstalling restores every memory
# automatically. The run ends by printing the package-removal command for the
# detected install method (uv tool / pipx / pip) — printed, never auto-run: a live
# process cannot reliably delete itself.

trailmem uninstall --purge
# DESTRUCTIVE: additionally deletes ~/.trailmem (every memory, irreversible) after a
# second typed 'purge' confirmation.
```

### MODEL Management (embedding model is user-configurable)

```bash
# List available + installed models
trailmem model list

# Install a supported model (checksum-verified download, NOT bundled in wheel)
trailmem model install bge-small     # default (384-dim, good balance)
trailmem model install minilm        # lighter (~200MB RAM)
trailmem model install nomic         # better quality (768-dim, ~500MB RAM)
trailmem model install --path /path/to/model.onnx   # custom ONNX; dims auto-detected at install and saved as dims.txt, no manual config edit

# Switch active model
trailmem model use nomic
# Config updated. Dimensions may change → reindex required (see below).

# Disable embeddings entirely → FTS5-only mode
trailmem model disable
# WARNING printed: semantic search OFF + near-duplicate detection OFF (exact-hash only)

# Re-embed all memories with the current model
# REQUIRED after `model use` when dimensions change (drops + recreates memories_vec)
trailmem reindex
# Also re-validates dedup bands against the new model's cosine distribution
```

Model config lives in `~/.trailmem/config.json`. Dedup thresholds (0.85/0.92) are per-model — swapping without `reindex` leaves stale bands. See [[schema]] embedding section and [[dedup]].

### HOST Integration

```bash
# One-line session status for a host statusline (resolves the host adapter's
# canonical session context; successful creates/edits count; always exit 0)
trailmem statusline
#   → 🧠 trailmem: N saved this session      (N > 0)
#   → ⚠ trailmem: 0 saved this session · /tm-save before exit   (N = 0)
```

See [[hooks]] for how this fits the save-awareness surfaces (`/tm-save`, welcome tip, next-session flag).

### HIDDEN Commands (rare, destructive)

```bash
# Hard delete — permanent removal (NOT recommended, use archive instead)
trailmem delete <ref> --hard --confirm
# Warning: edges CASCADE delete, knowledge trail lost
# Only for genuine junk/test data
```

---

## Reference Resolution

`<ref>` accepts both formats:
- `4` or `#4` → lookup by memory ID
- `mem-abc123` → lookup by node_id

```bash
trailmem show 4          # by ID
trailmem show mem-abc123 # by node_id
trailmem edit #4 --title "new"  # # prefix optional
```

---

## Output Format

Default: plain text (human + agent readable)
```bash
trailmem query "aria2"
# #12 [mem-abc123] [decision] [claude] QTcpSocket for aria2 Communication
#   Use QTcpSocket direct connection for aria2 JSON-RPC...
# #7 [mem-def456] [lesson] [archived] Qt WebSocket Failed
#   Connection drops under load, protocol mismatch...
```

JSON output (for dashboard/scripts):
```bash
trailmem list --format json
trailmem export --format json
```

---

## Environment Variables

| Variable | Purpose | Auto-fill |
|----------|---------|-----------|
| `TRAILMEM_AGENT_TYPE` | Generic agent identity / MCP host pin | Set by integration config |
| `TRAILMEM_SESSION_ID` | Generic stable external session ID | Optional; no value means stateless |
| Host-native session vars | Adapter input only | Declared in that host's module |
| `TRAILMEM_DB` | Custom DB path | Default: ~/.trailmem/trailmem.db |
| `TRAILMEM_HOME` | Custom home dir (config, models, DB, hooks.log) | Default: ~/.trailmem |
| `TRAILMEM_PROJECT` | Override project detection. Value must be an absolute path or the literal `global` (stores NULL for cross-project scope); a bare name is rejected. | Default: cwd |

---

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | General error |
| 2 | Validation error (content too short, missing title, etc) |
| 3 | Duplicate detected (exact hash match) |
| 4 | Near-duplicate blocked (>0.92 similarity, use --force) |

---

## Related

- [[mcp]] — the six MCP tools that share this CLI's validation/storage paths.
- [[schema]] — data contracts; `model`/`reindex` commands map to the embedding config.
- [[dedup]] — `similar` command + exit codes 3/4 behavior.
- [[hooks]] — `trailmem hook session-start/session-stop` host integration.
- [[migration]] — one-time seed runbook built on `store`/`link`.
