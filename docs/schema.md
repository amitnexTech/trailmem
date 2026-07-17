# trailmem — Schema Specification

## Overview

trailmem uses SQLite with 5 core tables: `memories` (core), `edges` (relationships), `memories_vec` (embeddings), `memories_fts` (full-text search), `sessions` (boundary tracking) — plus 2 dashboard support tables (`dashboard_state`, `dashboard_events`) populated by triggers for the [[dashboard]] SSE feed.

**Migrations:** `init_db` is idempotent (`CREATE ... IF NOT EXISTS`), so NEW tables/indexes/triggers self-heal on old DBs. Changes to EXISTING tables (e.g. `ALTER TABLE ... ADD COLUMN`) go in the append-only `MIGRATIONS` list in `schema.py`, tracked via `PRAGMA user_version` — each entry runs exactly once, in order, on every install. Never edit or reorder shipped entries.

## Connection Setup

```sql
-- CRITICAL: Every connection, first line. Without this, ON DELETE CASCADE silently fails.
PRAGMA foreign_keys = ON;
```

---

## Table: memories

```sql
CREATE TABLE memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    event_type TEXT NOT NULL DEFAULT 'memory',
    work_type TEXT,
    agent_type TEXT NOT NULL,
    project TEXT,
    session_id TEXT,
    source_uri TEXT,
    modified_files TEXT,
    pinned INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT,
    access_count INTEGER NOT NULL DEFAULT 0,
    last_accessed TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    archive_reason TEXT,
    content_hash TEXT NOT NULL
);
```

### Field Specifications

| Field | Validation | Auto/Agent | Notes |
|-------|-----------|------------|-------|
| id | Auto-increment | System | Never reuse deleted IDs. Gaps allowed. |
| node_id | `mem-<8hex>` format, UNIQUE | System | Immutable. Edges reference this. Never changes on export/import. |
| title | 3-60 chars, mandatory | Agent MUST provide | Short descriptive label. Not auto-extracted. |
| content | 50-4000 chars (soft warn >4000, no reject) | Agent MUST provide | Main memory body. Plain text + newlines + bullets. |
| event_type | See enum below | Agent MUST provide | Category of knowledge. |
| work_type | See enum below, nullable | Agent optional | Activity type that produced this memory. |
| agent_type | See enum below | System auto-fill from env, agent override allowed | Who wrote it. |
| project | Absolute path or NULL | System auto-fill from cwd, NULL=global | Scope isolation. An explicit project (param/`TRAILMEM_PROJECT`) must be an **absolute path** or the literal `"global"` — a bare name (e.g. `jarvis_build`) is **rejected**, since it silently splits a project's memories from its cwd-derived absolute-path form. Omit to auto-fill from cwd. |
| session_id | Free text, nullable | System auto-fill from env var | Groups work within one session. |
| source_uri | Free text, nullable | Agent optional | Origin: file path, session ref, URL. |
| modified_files | Comma-separated paths, nullable | Agent optional | Files touched in this work. |
| pinned | 0 or 1 | Agent sets | 1 = always in welcome, never buried. |
| created_at | ISO 8601 timestamp | System | Immutable after creation. Stored in **UTC** (`datetime.now(UTC)`); human-facing surfaces (CLI, welcome, dashboard) render it in the **system local timezone** via `store.fmt_local()` so a memory made near UTC midnight is not shown on the wrong day. |
| updated_at | ISO 8601 timestamp, nullable | System (on edit) | Set when content/title changes. |
| access_count | Integer >= 0 | System (on query result) | NOT incremented on welcome. Only explicit queries. |
| last_accessed | ISO 8601 timestamp, nullable | System (on query result) | |
| status | active/archived/superseded | Agent explicit action | Default: active. |
| archive_reason | Min 20 chars when status != active | Agent (mandatory on archive) | WHY archived/superseded. App-enforced, not DB CHECK. |
| content_hash | SHA256(content) | System | For exact-duplicate detection. |

### event_type Enum (application-validated, not DB CHECK)

| Type | Use For | Pin-worthy? |
|------|---------|-------------|
| `decision` | Choices made, enforced rules, architecture calls | Often |
| `lesson` | Experience-based learnings from past work | Sometimes |
| `error_pattern` | Recurring failure signatures to avoid | Sometimes |
| `task` | Planned work items / discovered issues to fix | No |
| `memory` | Events, what happened (session logs) | No |
| `user_preference` | Personal choices (language, style, communication) | Often |
| `constraint` | Hard "NEVER break" rules. Break = damage. | Auto-pinned always |
| `session_summary` | Session-end recaps of work done | No |

**Constraint criteria (strict):**
- Only "break = real damage" rules qualify
- Test: "Agar ye rule break ho to DAMAGE hoga?" → Yes = constraint, No = decision
- NOT for: tool guides, preferences, workflow suggestions

### work_type Enum (nullable)

```
discussion, file-edit, code-written, bug-fix, research, setup, review
```

Orthogonal to event_type. event_type = what kind of knowledge. work_type = what activity produced it.

### agent_type Enum

```
kiro, claude, codex, opencode, kilo, antigravity, user
```

`user` = manual CLI/dashboard entry by human.

### Auto-fill Logic

```python
def auto_fill(agent_input, env):
    filled = {}
    
    # System auto-fill (agent CANNOT override):
    filled['node_id'] = f"mem-{secrets.token_hex(4)}"
    filled['created_at'] = datetime.now(UTC).isoformat()
    filled['content_hash'] = hashlib.sha256(agent_input['content'].encode()).hexdigest()
    
    # System auto-fill (agent CAN override):
    agent = agent_input.get('agent_type') or env.get('TRAILMEM_AGENT_TYPE') or detect_from_env()
    if not agent:
        # LOUD reject — never store a NULL/"unknown" agent (this was OMEGA's exact bug:
        # ~20 rows landed with agent_type NULL because detection silently failed).
        raise ValidationError(
            "agent_type could not be determined (no param, no TRAILMEM_AGENT_TYPE, "
            "no known session env var). Pass agent_type explicitly. Refusing to store unattributed."
        )
    filled['agent_type'] = agent
    filled['project'] = agent_input.get('project') or os.getcwd()
    filled['session_id'] = agent_input.get('session_id') or env.get('CLAUDE_CODE_SESSION_ID') or env.get('KIRO_SESSION_ID')
    
    return filled
```

**`agent_type` is `NOT NULL` AND undetectable → hard reject, not silent "unknown".** `agent_type` has a required-with-fallback contract: auto-detect where possible, explicit override always allowed, but if BOTH fail the store errors rather than writing an unattributed row. This is the structural fix for the original NULL-agent bug — the rule lives in code, not in a reminder the agent can forget. (`project` auto-fills from cwd when omitted and `session_id` degrades to NULL; but an *explicitly supplied* non-absolute, non-`global` project hard-rejects — only `agent_type` hard-rejects on the detect path.)

---

## Table: edges

```sql
CREATE TABLE edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_node_id TEXT NOT NULL,
    target_node_id TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    metadata TEXT,
    created_at TEXT NOT NULL,
    
    FOREIGN KEY (source_node_id) REFERENCES memories(node_id) ON DELETE CASCADE,
    FOREIGN KEY (target_node_id) REFERENCES memories(node_id) ON DELETE CASCADE
);
```

### edge_type Enum

| Type | Meaning | Direction |
|------|---------|-----------|
| `related` | Connected topics | A → B (bidirectional intent, stored one-way) |
| `derived_from` | B was born from A | A → B |
| `supersedes` | B replaces A (A is now outdated) | B → A (newer points to older) |
| `contradicts` | A and B conflict (needs resolution) | A → B |
| `evolves` | B is refinement of A (A was correct then, B is updated) | B → A |

**`supersedes` vs `evolves`:**
- supersedes = old was WRONG/DROPPED
- evolves = old was correct at the time, new is updated version

**Note:** `superseded_by` is NOT a column. Derive from edges:
```sql
SELECT source_node_id FROM edges WHERE target_node_id = ? AND edge_type = 'supersedes'
```

---

## Table: memories_vec (Vector Search)

```sql
-- Dimensions from config (default 384 for bge-small-en-v1.5)
-- RECREATED on model swap (trailmem reindex drops + recreates with new dims)
CREATE VIRTUAL TABLE memories_vec USING vec0(
    node_id TEXT,
    embedding float[N]   -- N = config.embedding.dimensions (384/768/etc)
);
```

**CRITICAL:** 
- Links via `node_id`, NEVER via `id`. IDs can change on import/export; node_ids never change.
- Not cascaded by SQLite. App-code must manually delete vec row when memory deleted.
- Dimensions are MODEL-DEPENDENT. `trailmem reindex` = DROP table + recreate with new dims + re-embed all.

---

## Table: memories_fts (Full-Text Search)

```sql
CREATE VIRTUAL TABLE memories_fts USING fts5(
    node_id UNINDEXED,
    title,
    content
);
```

**Standalone, app-managed** — NOT external-content mode. No triggers needed.

App-code syncs FTS on:
- INSERT → add to FTS
- UPDATE content/title → delete old + insert new in FTS
- DELETE → delete from FTS

**NOT synced on:** access_count/last_accessed updates (performance — no re-index on reads).

---

## Table: sessions (Boundary Tracking)

```sql
CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    agent_type TEXT NOT NULL,
    project TEXT,
    started_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_welcome_at TEXT
);
```

**Purpose:** Track session boundaries for "since your last session" delta. No junk memories.

**Fields:**
- `started_at` — BOUNDARY marker. Set ONCE on first `trailmem_*` call. IMMUTABLE after that.
- `last_seen_at` — Activity marker. Updated on welcome + stop-hook only. For purge/stale detection.
- `last_welcome_at` — Anti-bloat. Tracks when welcome was last served this session.

**Key Rules:**
- Boundary query uses `started_at` ONLY — never `last_seen_at`
- Boundary query MUST exclude current session_id: `WHERE session_id != current`
- `started_at` NEVER updated after initial set (INSERT ON CONFLICT updates only last_seen_at)
- Session row created on FIRST `trailmem_*` call (lazy, not welcome-only)
- Stop-hook is NON-CRITICAL — boundary safe from started_at regardless

**Behavior:**
```sql
-- First trailmem_* call (any tool): register session (lazy)
-- CRITICAL INVARIANT: last_welcome_at NEVER set here. Only trailmem_welcome writes it.
INSERT INTO sessions (session_id, agent_type, project, started_at, last_seen_at)
VALUES (?, ?, ?, now(), now())
ON CONFLICT(session_id) DO UPDATE SET last_seen_at = excluded.last_seen_at;
-- started_at NOT in DO UPDATE — never clobbered
-- last_welcome_at NOT touched — preserves anti-bloat logic

-- Welcome call (ATOMIC — BEGIN IMMEDIATE for write-lock, prevents concurrent race):
BEGIN IMMEDIATE;
SELECT last_welcome_at FROM sessions WHERE session_id = ?;  -- read PRIOR value
UPDATE sessions SET last_welcome_at = ?, last_seen_at = ? WHERE session_id = ?;
COMMIT;
-- PRIOR NULL → full welcome (first time). PRIOR NOT NULL → short (pinned+stats).

-- Boundary fetch (BEFORE registering in welcome, exclude current):
SELECT MAX(started_at) FROM sessions 
WHERE agent_type = ? AND session_id != ?;
```

**Maintenance:** Sessions >90 days old auto-purged in `trailmem maintain --apply`.

---

## Embedding Model (Q15 — LOCKED)

**Default: `bge-small-en-v1.5` (ONNX, 384-dim). User-configurable — not hardcoded.**

Open-source tool: the user picks the model. trailmem ships a good default and a swap path, never binds the user to one choice.

| Factor | Why bge-small is the default |
|---|---|
| Proven | Same model ran OMEGA's semantic search on this machine — quality already validated in real use |
| Cost | Local ONNX on CPU, zero API cost, ~130MB on disk, fast enough for per-store top-1 similarity |
| Balance | 384-dim = fast similarity search + good MTEB quality for its size; English-optimized (fits the English-only content rule) |

Supported out of the box (via `trailmem model install <name>`): `bge-small` (default), `minilm` (lighter, ~200MB RAM), `nomic` (better, 768-dim, ~500MB RAM), custom ONNX (`--path`; dimensions auto-detected at install and saved to the model dir, no manual config), or none (`trailmem model disable` → FTS5-only).

Rules:
- Config lives in `~/.trailmem/config.json` (`embedding.model`, `embedding.dimensions`, `embedding.enabled`). Model files under `~/.trailmem/models/`, downloaded by `trailmem setup` / `model install` (checksum-verified). **Never bundled in the pip wheel** (127MB+ would bloat every install and PyPI rejects large wheels).
- **Dimensions are model-dependent, NOT hardcoded.** `memories_vec` is created with `float[N]` where N = `config.embedding.dimensions` (384 for bge-small, 768 for nomic). Model swap → `trailmem reindex` DROPs + recreates the vec table with the new dims + re-embeds all content.
- **Dedup thresholds (0.85/0.92) are per-model, in config.** Different models have different cosine distributions — the same thresholds over-block or under-warn on a different model. Per-model defaults ship; `reindex` after a model swap must re-validate the bands, else dedup silently degrades.
- Fallback chain: configured model → (absent/disabled) keyword-only mode — FTS still works, dedup degrades to hash-only (near-duplicate detection OFF, user warned explicitly on `model disable`), welcome unaffected. `trailmem doctor` flags degraded mode. **No hash-embedding pseudo-vectors** (OMEGA's fallback silently gave garbage similarity — degrade loudly instead).

### Content language rule
Memory **content is English-only** (soft warn, not hard reject). The agent communicates with the user in their language (e.g. Hinglish) but stores the English version — embedding accuracy + search reliability depend on it.

**Heuristic limitation (important):** an ASCII-ratio check catches Devanagari/CJK but is BLIND to Roman-script Hinglish ("wayfire me popup dismiss kaam nahi kar raha" is 100% ASCII → passes). So:
- Supplement with an English stopword-density check (the/is/of/and — low density flags likely non-English Roman text). Cheap, imperfect.
- **Primary enforcement is NOT the validator** — it is a pinned `constraint` memory ("memory content = English") surfaced in every welcome, so the rule is seen each session rather than relying on a heuristic that its main case defeats.
- Soft-warn only (never block) — code with unicode identifiers, proper nouns, etc. are legitimate edge cases.

---

## Indexes

```sql
CREATE INDEX idx_memories_hash_project ON memories(content_hash, project);
CREATE INDEX idx_memories_status_pinned ON memories(status, pinned);
CREATE INDEX idx_memories_project ON memories(project);
CREATE INDEX idx_memories_event_type ON memories(event_type);
CREATE INDEX idx_memories_agent ON memories(agent_type);
CREATE INDEX idx_memories_created ON memories(created_at DESC);
CREATE INDEX idx_edges_source ON edges(source_node_id);
CREATE INDEX idx_edges_target ON edges(target_node_id);
CREATE UNIQUE INDEX idx_edges_unique ON edges(source_node_id, target_node_id, edge_type);
```

---

## App-Logic Contracts (DB does NOT enforce these)

| Operation | Must Do (all-or-nothing) |
|-----------|--------------------------|
| **store** | validate title/content → dup-check (hash+project) → embedding similarity warn (>0.85) → INSERT memories + memories_vec + memories_fts (all three) |
| **edit content** | recompute content_hash + regenerate embedding (update vec) + update fts + set updated_at — all four bound |
| **delete** | DELETE from memories + memories_vec + memories_fts (vec/fts NOT cascaded); edges auto-cascade (if FK ON) |
| **archive/supersede** | archive_reason ≥20 chars + ≥1 edge exists — else reject |
| **query result** | access_count += 1 + last_accessed = now (welcome NOT counted) |
| **every connection** | PRAGMA foreign_keys = ON — first line |
| **superseded_by lookup** | No column — derive: SELECT source FROM edges WHERE target=? AND edge_type='supersedes' |

---

## Related specs

- [[welcome]] — how sessions/pinned/boundary tables drive the briefing.
- [[dedup]] — content_hash + embedding duplicate policy on store.
- [[evolution]] — status/archive_reason/supersedes edge lifecycle.
- [[mcp]] — the tool surface that writes this schema.
- [[cli]] — command surface, including `trailmem model` + `reindex` for the embedding config above.
