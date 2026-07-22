# trailmem — Seed Playbook (Omega → trailmem One-Time Transfer)

Runbook for the one-time, human-reviewed data seed from Omega into trailmem through the normal `trailmem store`/`link` path — no shipped importer.

**Status:** REFERENCE

> **Not the SQL schema-migration doc.** This page is the one-time *data* seed from Omega. For DB schema migrations (the `MIGRATIONS` list, `PRAGMA user_version`, `ALTER TABLE` on upgrade), see [[schema]] → “Migrations”.

## Approach

**No shipped `--from-omega` importer.** Migration is a one-time manual seed through normal `trailmem store` path. This ensures:
- All validation runs (dedup, English check, title mandatory, linking)
- Quality control (human judges titles, types, links — no auto-junk)
- Real dogfood test of trailmem's store/link/dedup on first use
- No vendor-coupling code in open-source package

## Steps

### 1. Extract from Omega (throwaway read-only script)

```python
# One-time script, NOT shipped with trailmem
import sqlite3, json
conn = sqlite3.connect("~/.omega/omega.db")
cur = conn.cursor()
cur.execute("SELECT id, content, event_type, agent_type, project FROM memories ORDER BY id")
for row in cur.fetchall():
    print(json.dumps({"id": row[0], "content": row[1], "type": row[2], "agent": row[3], "project": row[4]}))
```

Review output. Decide which memories to keep (some are junk/noise — skip those).

### 2. Setup trailmem

```bash
trailmem setup           # creates ~/.trailmem/, downloads model
trailmem doctor          # verify all healthy
```

### 3. Seed memories (with judgment)

For each kept memory, store through normal path:
```bash
trailmem store "English content here" \
    --title "Proper Title" \
    --type decision \
    --agent kiro \
    --pin  # if it's a rule/constraint
```

**Key:** Titles written fresh (OMEGA had none). Content cleaned to English. Types corrected. This is judgment work, not bulk copy.

### 4. Create edges (meaningful only)

```bash
trailmem link <new-ref-A> <new-ref-B> --type related --reason "tools usage in rules"
```

**Drop OMEGA's `temporal_cluster` auto-edges.** Only recreate edges that represent real relationships.

### 5. Verify

```bash
trailmem list --orphans    # should be 0
trailmem stats             # counts look right
trailmem welcome           # briefing makes sense
trailmem dashboard         # visual check
```

### 6. Cutover

1. Register trailmem MCP in `.mcp.json` — REMOVE omega-memory entry in same edit.
2. Update hooks (session-start/stop → trailmem commands).
3. Update steering files (references to omega → trailmem).
4. Keep `~/.omega/` untouched 30 days (rollback window), then archive/delete.

**CRITICAL:** Never run two memory systems in parallel (omega + trailmem) — divergence guaranteed.

## What's NOT in this playbook

- No `--from-omega` CLI command (not shipped, not maintained)
- No automatic title generation (titles = human judgment)
- No node_id preservation (fresh IDs, clean break)
- No auto-edge import (only meaningful edges recreated manually)
- No content-length waiver (if >4000, split or summarize during seed)

## Timeline

This is a ~30 minute manual job for ~30 memories. Not worth automating.

---

## Related

- [[cli]] — `trailmem store` / `trailmem link` commands the seed runs through.
- [[schema]] — field contracts every seeded record must satisfy.
- [[dedup]] — the duplicate policy that applies during seeding (no bypass).
- [[evolution]] — recreating only meaningful `supersedes`/`evolves`/`related` edges.
