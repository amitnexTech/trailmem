# trailmem — CLI Reference

## Overview

`trailmem` is the command-line interface for managing agent memory. All operations available via MCP tools are also available via CLI.

**Installation:** `pip install trailmem`
**Binary:** `trailmem`

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

# Edit existing memory
trailmem edit <ref> --content "updated content"
trailmem edit <ref> --title "new title"
trailmem edit <ref> --type lesson

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
# Starts web UI at http://127.0.0.1:3800

# Setup (first-time)
trailmem setup
# Creates ~/.trailmem/, downloads embedding model, registers MCP

# Health check
trailmem doctor
# Verifies: DB, model, MCP registration, indexes
```

### MODEL Management (embedding model is user-configurable)

```bash
# List available + installed models
trailmem model list

# Install a supported model (checksum-verified download, NOT bundled in wheel)
trailmem model install bge-small     # default (384-dim, good balance)
trailmem model install minilm        # lighter (~200MB RAM)
trailmem model install nomic         # better quality (768-dim, ~500MB RAM)
trailmem model install --path /path/to/model.onnx   # custom ONNX

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
| `TRAILMEM_PROJECT` | Override project detection | Default: cwd |

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
