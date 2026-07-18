# trailmem — MCP Server Specification

## Overview

6 MCP tools over stdio transport. All responses = plain text (agent-facing, token-efficient). JSON consumers use the CLI (`trailmem query/list/export --format json`) or the dashboard's own service layer — the MCP tools deliberately ship no format parameter (v1).

**Token cost:** ~1200 tokens for tool schemas (vs Omega's ~5000).

---

## Server Architecture

### Transport
**stdio only (v1).** No HTTP server. Multiple agents on same machine = each spawns own stdio process, all share one WAL-mode SQLite DB. By design.

HTTP deferred to future (team use case). Adding later is backward-compatible.

### Lifecycle
**On-demand spawn.** MCP host (Claude Code/Kiro/Codex) starts the process, communicates, kills on session end. No background daemon, no keep-alive.

**ONNX model: lazy-load.** Model NOT loaded at server start. Loaded on first call that needs embedding (store/query). Welcome = instant (no embedding needed). Cold-start penalty: ~1-2s on first store/query, zero after.

### Concurrency (Multi-Agent)
```sql
-- Every connection, first two lines:
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 3000;  -- wait up to 3s on write contention (WAL handles rest)
```

- SQLite WAL mode — multiple readers + one writer at a time
- `busy_timeout=3000` — prevents instant SQLITE_BUSY errors on contention
- `BEGIN IMMEDIATE` — on read-modify-write operations (welcome anti-bloat, dup-check+insert)
- No extra file-locks, queues, or coordinators needed
- Two agents, one machine, human-speed writes — WAL handles this natively

### Tool Description Format (in MCP schema)
Each tool description = **2-3 lines, fixed structure:**
- Line 1: What it does (action)
- Line 2: When to use / boundary rule
- Line 3: Non-obvious clarification (only if needed)

No inline examples in descriptions. Response format is self-documenting.

---

## Tool 1: `trailmem_welcome`

Session start briefing. Anti-bloat protected.

**Parameters:**
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| project | string | No | auto from cwd | Project scope filter |
| agent_type | string | No | auto from env | Identity for boundary query + session tracking |
| force | boolean | No | false | Bypass anti-bloat, full welcome again |

**Response (plain text):**
- First call: Full welcome (7 sections: pinned/last-activity/your-last/since/tasks/action/stats)
- 2nd+ call: Short (pinned + constraints full content + stats)
- force=true: Full welcome regardless

**Behavior:**
1. Fetch boundary (exclude current session_id)
2. Read prior last_welcome_at (BEGIN IMMEDIATE transaction)
3. Update session row (set last_welcome_at + last_seen_at)
4. Prior NULL → full, Prior NOT NULL → short (unless force)
5. access_count NOT incremented on any memory shown

---

## Tool 2: `trailmem_store`

Save new memory with optional linking + supersede in one call.

**Parameters:**
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| title | string | YES | — | 3-60 chars, descriptive label |
| content | string | YES | — | 50+ chars (soft warn >4000) |
| event_type | string | YES | — | decision/lesson/error_pattern/task/memory/user_preference/constraint/session_summary |
| agent_type | string | No | auto from env | kiro/claude/codex/opencode/kilo/antigravity/zed/cursor/windsurf/user |
| project | string | No | auto from cwd | NULL = global. If supplied, must be an absolute path or `"global"`; a bare name is rejected. |
| work_type | string | No | null | discussion/file-edit/code-written/bug-fix/research/setup/review |
| source_uri | string | No | null | Origin reference |
| modified_files | string | No | null | Comma-separated paths |
| pinned | boolean | No | false | Pin this memory |
| link_to | string/array | No | null | ref(s) to link to (#id or node_id) |
| edge_type | string | No | "related" | related/derived_from/supersedes/contradicts/evolves |
| supersedes | string/int | No | null | ref of memory this replaces |
| archive_reason | string | No | null | REQUIRED when supersedes set (min 20 chars) |
| force | boolean | No | false | Bypass >0.92 similarity block |

**Response (plain text):**
```
Success:      "Stored #12 [mem-abc123] 'QTcpSocket Decision'. Linked to #4."
Exact dup:    "Rejected: exact duplicate of #4 [mem-xyz] 'Title'. Use trailmem_edit(ref=#4)."
Blocked:      "Blocked: 94% similar to #4 [mem-xyz] 'Title'. trailmem_edit(ref=#4) or force=true."
Warned:       "Stored #12 [mem-abc123]. ⚠ 88% similar to #4 [mem-xyz]. Consider linking."
Soft warn:    "Stored #12 [mem-abc123]. ⚠ Content 4200 chars — consider docs/ for detail."
```

**Processing order:**
1. Validate title (3-60) + content (50+)
2. Auto-fill agent_type/project/session_id
3. content_hash + project dup check (exact → reject)
4. Embedding similarity (>0.92 block, 0.85-0.92 warn) — skip if force=true
5. INSERT memories + memories_vec + memories_fts (all three, atomic)
6. If link_to → create edges
7. If supersedes → archive old (set status, archive_reason) + create supersedes edge

---

## Tool 3: `trailmem_query`

Search memories by semantic + keyword.

**Parameters:**
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| text | string | YES | — | Search query |
| type_filter | string | No | null | Filter by event_type |
| agent_filter | string | No | null | Filter by agent_type |
| project | string | No | auto from cwd | Scope (global always included) |
| limit | int | No | 5 | Max results |
| include_archived | boolean | No | true | Include archived/superseded |

**Response (plain text):**
```
#12 [mem-abc123] [decision] [claude] [active] [↔3] QTcpSocket for aria2
  Use QTcpSocket direct connection for aria2 JSON-RPC...
  
#7 [mem-def456] [lesson] [claude] [archived] [↔2] Qt WebSocket Failed
  Connection drops under load, protocol mismatch...

#19 [mem-xyz] [error_pattern] [kiro] [active] [↔0] Some Orphan Memory
  ...

(3 results for "aria2 communication")
```

**IMPORTANT:** Query NEVER returns edges. Edges = trailmem_show only. This prevents query response bloat.

**Edge-count indicator:** Each result includes `[↔N]` showing edge count — agent sees connectivity at a glance without loading edges. `[↔0]` = orphan signal.

**Behavior:**
1. Generate embedding for query text
2. Vec similarity (top 20) + FTS keyword (top 20)
3. Merge + re-rank (vector 0.7 + FTS 0.3)
4. Archived/superseded = 0.5x weight
5. Apply filters (type, agent, project + global)
6. access_count += 1 for returned results
7. Return top N, each with: #id [node_id] [type] [agent] [status] title + content preview (200 chars)

---

## Tool 4: `trailmem_show`

Direct fetch: one memory's full content + ALL edges + supersede chain.

**Parameters:**
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| ref | string/int | YES | — | #id or node_id of memory |

**Response (plain text):**
```
#7 [mem-def456] Qt WebSocket Failed
  Type: lesson | Agent: claude | Status: archived
  Created: 2026-07-10 | Updated: 2026-07-12
  Access count: 8 | Pinned: no
  Archive reason: "Replaced by QTcpSocket — connection drops under load"
  
  Content:
  Qt WebSocket approach for aria2 JSON-RPC failed. Connection drops 
  under sustained load. aria2's WebSocket protocol implementation...
  
  Edges (3):
  [e3] → OUT #12 [mem-abc123] [supersedes] "replaced by QTcpSocket approach"
  [e7] ← IN  #5 [mem-ghi789] [derived_from] "discovered during download testing"
  [e9] ← IN  #4 [mem-jkl012] [related] "tools reference"
  
  Supersede chain:
  #18 (active) → supersedes → #12 (superseded) → supersedes → #7 (this, archived)
  Current: #18 "HTTP JSON-RPC for aria2"
```

**IMPORTANT:**
- `trailmem_show` is the ONLY tool that returns edges and supersede chains.
- Edge ids (`[e3]`) MUST be displayed — `trailmem_link(action="remove")` requires edge_id, and show is the only place an agent can discover it.
- `trailmem_query` NEVER returns edges (prevents bloat on multi-result searches).
- Accepts both #id and node_id (resolve_memory_ref reuse).
- access_count += 1 (read operation, same as query).

**Behavior:**
1. Resolve ref (accept #id or node_id)
2. Fetch full memory row
3. Fetch all edges (in + out)
4. If status = superseded/archived, trace supersede chain (both directions)
5. access_count += 1
6. Format full detail response

---

## Tool 5: `trailmem_edit`

Update existing memory or change its status.

**Parameters:**
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| ref | string/int | YES | — | #id or node_id |
| content | string | No | — | New content |
| title | string | No | — | New title |
| event_type | string | No | — | Change type |
| pinned | boolean | No | — | Pin/unpin |
| status | string | No | — | "archived" or "superseded" |
| archive_reason | string | No | — | REQUIRED for archive/supersede (min 20 chars) |
| link_to | string/array | No | — | Add edges during edit |
| edge_type | string | No | "related" | Type for new edges |

**Response (plain text):**
```
Content edit:  "Updated #4 [mem-xyz] 'Workflow Rules'. Content + hash + embedding refreshed."
Archive:       "Archived #7 [mem-abc] 'Old Decision'. Reason: 'replaced by...'. Linked to #12."
Pin:           "Pinned #4 [mem-xyz] 'Workflow Rules'."
Error:         "Cannot archive: archive_reason required (min 20 chars)."
Error:         "Cannot archive: no edges exist. Link to related memory first (trailmem_link)."
```

**Behavior (content edit ripple — all-or-nothing):**
- Content change → recompute content_hash + regenerate embedding + update FTS + set updated_at
- Status change → validate archive_reason (≥20) + validate ≥1 edge exists
- Title/type/pin → simple UPDATE + updated_at

---

## Tool 6: `trailmem_link`

Create or remove edges between memories.

**Parameters:**
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| action | string | YES | — | "add" or "remove" |
| source | string/int | No* | — | Source ref (*required for "add") |
| target | string/int | No* | — | Target ref (*required for "add") |
| edge_type | string | No* | — | *required for "add": related/derived_from/supersedes/contradicts/evolves |
| metadata | string | No | "" | Reason/description |
| edge_id | int | No* | — | *required for "remove" |

**Response (plain text):**
```
Add:         "Linked #4 → #7 [related] 'tools defined in rules'."
Add dup:     "Edge already exists: #4 → #7 [related]. No action."
Remove:      "Unlinked edge #3 (#4 → #7 [related])."
Orphan warn: "Unlinked edge #3. ⚠ #7 now has 0 edges (orphan). Consider linking."

(Unknown ref "#99 not found" and self-link attempts are MCP protocol
errors per Cross-Tool Error Handling — not plain-text responses.)
```

**Behavior:**
- Add: validate both refs exist, not same, insert edge (UNIQUE constraint prevents exact dup)
- Remove: delete by edge_id. If target memory becomes orphan → warn
- source/target accept both #id and node_id

---

## Cross-Tool Rules

| Rule | Detail |
|------|--------|
| Response format | Plain text always (v1). JSON consumers use CLI `--format json` (query/list/export) or the dashboard service layer. |
| ID display | Always `#id [node_id]` in every response. Agent can use either for next call. |
| Edge-count | `[↔N]` shown in query/welcome results. N = total edges. [↔0] = orphan signal. |
| [pinned] tag | Shown in query results if memory is pinned — signals "load-bearing, don't casually edit". |
| Ref resolution | All `ref` params accept both `#4`/`4` (by id) and `mem-abc123` (by node_id). |
| access_count | Incremented on: query results, show. NOT on: welcome, store, edit, link. |
| Session registration | First trailmem_* call (any tool) lazy-creates session row. last_welcome_at NEVER set by non-welcome tools. |
| WAL + busy_timeout | Every connection: PRAGMA foreign_keys=ON + PRAGMA busy_timeout=3000. |
| BEGIN IMMEDIATE | Used on read-modify-write transactions (welcome anti-bloat, dup-check+insert). |

### Error Handling

| Error Type | Response Method | Example |
|-----------|----------------|---------|
| Invalid params (missing required, wrong type, unknown ref) | **MCP protocol error** | ref #99 not found |
| Business outcome (dup reject, similarity block, archive needs reason) | **Plain text in success response** + next-action hint | "Blocked: 94% similar to #4. trailmem_edit(ref=#4) or force=true." |

**Rule:** Every business-outcome text MUST include next-action instruction. Agent should never be stuck after a rejection.

**Why not protocol-error for dup/block:** Some MCP clients auto-retry on protocol errors. Dup-reject → retry = infinite loop. Business outcomes are information, not failure.

### Hook Integration
Beyond the 6 tools, the server exposes one MCP **prompt**, `save_session` (title “Save this session to memory”): a portable, zero-config way for any prompt-aware client (Claude Code, Cursor, VS Code, Windsurf) to have the live agent extract the session's decisions/lessons/tasks and call `trailmem_store`. It carries no side effects itself — it just returns the capture instruction. See [[hooks]] “Save-awareness” for the full trigger/reminder model. Session lifecycle hooks are documented separately in [[hooks]].
Hook registration = per-host config (Claude Code settings.json, Kiro .kiro/settings/, Codex hooks.json).

---

## Related specs

- [[schema]] — tables/contracts these tools read and write.
- [[welcome]] — the seven-section briefing `trailmem_welcome` renders.
- [[dedup]] — `trailmem_store` duplicate bands + store-time link assistance.
- [[evolution]] — supersede/archive semantics behind `store`/`edit`.
- [[hooks]] — session lifecycle integration.
- [[cli]] — the equivalent command-line surface.
