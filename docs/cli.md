# trailmem — CLI Reference

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
#       --modified-files "a.py,b.py", --force to bypass the >0.92 near-dup block (exit 4)

# Edit existing memory
trailmem edit <ref> --content "updated content"
trailmem edit <ref> --title "new title"
trailmem edit <ref> --type lesson
trailmem edit <ref> --pin            # or --no-pin
trailmem edit <ref> --status archived --reason "why (min 20 chars)" --link-to <ref>

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
# Antigravity / Zed / Cursor / Windsurf
# via their MCP config files) and, ONLY after an explicit y/N prompt, writes each host's
# own MCP config. Per-host config differs: Claude Code uses `claude mcp add`; others get
# their JSON config patched. No silent changes. (Manual fallback: `claude mcp add trailmem -- trailmem-mcp`.)
# ANY other MCP agent works manually: stdio transport, command `trailmem-mcp`, no args/env.
# README has the generic guide ("Any other MCP agent") with the common JSON shape + `which trailmem-mcp` for the absolute path.

# Update to a newer release
uv tool upgrade trailmem   # (or: pipx upgrade trailmem / pip install --upgrade trailmem inside a venv)
# No in-app "update available" notice (no telemetry, by design). Track GitHub Releases.

# Health check
trailmem doctor
# Verifies: home + config presence, DB tables, sqlite-vec extension, embedding model
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
# One-line session status for a host statusline (reads session_id from stdin
# JSON or CLAUDE_CODE_SESSION_ID/KIRO_SESSION_ID env; read-only, always exit 0)
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
| `TRAILMEM_AGENT_TYPE` | Default agent_type | Auto-detects from CLAUDE_CODE_SESSION_ID etc |
| `CLAUDE_CODE_SESSION_ID` | Session tracking | Claude Code sets this |
| `KIRO_SESSION_ID` | Session tracking | Kiro sets this |
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

## Related specs

- [[mcp]] — the six MCP tools that share this CLI's validation/storage paths.
- [[schema]] — data contracts; `model`/`reindex` commands map to the embedding config.
- [[dedup]] — `similar` command + exit codes 3/4 behavior.
- [[hooks]] — `trailmem hook session-start/session-stop` host integration.
- [[migration]] — one-time seed runbook built on `store`/`link`.
