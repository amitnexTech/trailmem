# trailmem — Duplicate Detection

## Overview

4-band similarity system. Not blanket accept/reject — response depends on how similar the new memory is to existing ones.

## Processing Order (cheap → expensive)

```
Step 1: content_hash + project check (instant, no model)
        → Exact match? REJECT immediately.
        
Step 2: Embedding similarity (requires model — SKIPPED if model disabled)
        → Compute embedding of new content
        → Find top-1 similar existing memory
        → Apply band rules below (thresholds are PER-MODEL from config)
        
        If model disabled (FTS5-only mode): skip to Band 4 (silent accept).
        Only exact-hash catches duplicates. User informed on model disable.
```

Never run Step 2 if Step 1 already matched (save compute).

---

## 4-Band Similarity Actions

### Band 1: Exact Hash Match

```
Condition: content_hash matches AND same project scope
Action: REJECT
Response: "Exact duplicate of #X [mem-abc123] 'Title'. 
           Use trailmem_edit(id=X) to update, or change content."
```

**Note:** Same content in DIFFERENT project = allowed (global + scoped can coexist).

### Band 2: Very High Semantic Similarity (>0.92)

```
Condition: Embedding cosine similarity > 0.92
Action: BLOCK (default) + suggest edit
Response: "Near-duplicate of #X [mem-abc123] 'Title' (94% similar).
           Suggested: trailmem_edit(id=X) to update existing.
           Or pass force=true to store anyway."
```

**Override:** `force=true` parameter bypasses block. For legit near-duplicates (same pattern in different apps).

### Band 3: Medium Similarity (0.85 — 0.92)

```
Condition: Embedding cosine similarity 0.85-0.92
Action: WARN + accept (store happens)
Response: "Stored as #Y [mem-def456]. 
           Note: Similar to #X [mem-abc123] 'Title' (88%).
           Consider: trailmem_link(source=Y, target=X, type='related')"
```

### Band 4: Low Similarity (<0.85)

```
Condition: Embedding cosine similarity < 0.85
Action: Silent accept
Response: "Stored as #Y [mem-def456]."
```

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Hash check first | Instant, free. No model needed. Catches exact copies. |
| Hash includes project scope | Same fact legitimately exists in multiple projects. |
| >0.92 blocks by default | Near-certain same fact different wording. Prevent bloat. (Threshold is per-model.) |
| Override available | Sometimes legit (same bug pattern in 2 apps). Never hard-reject. |
| 0.85-0.92 warns | Related but maybe different. Agent decides. (Band is per-model.) |
| <0.85 silent | Clearly different. No noise. |
| Existing #id always returned | Agent can immediately edit/link without extra query. |
| Thresholds per-model | Different models have different cosine distributions. Swap model = bands auto-adjust from config. |
| FTS5-only mode | No semantic dedup (only hash). User explicitly warned on model disable. |

---

## Store-Time Link Assistance (structural no-orphan enforcement)

The dedup embedding pass ALREADY computes similarity to existing memories — reuse it to fight orphans and blind linking at store time, instead of leaving both to agent discipline (which OMEGA proved fails).

### Top-N related suggestion (every store, not just the warn band)

On every store (any band, including <0.85), return the top 1-3 nearest neighbours as link candidates:

```text
Stored #Y [mem-def456].
Related candidates: #4 [mem-abc] (0.71), #9 [mem-ghi] (0.63)
  → trailmem_link if connected, or ignore.
```

If the store lands with ZERO neighbours above a low floor (e.g. 0.3), emit an explicit orphan warning:

```text
Stored #Y [mem-def456]. ⚠ No related memories found — this is an orphan. Link it or confirm it is standalone.
```

Rationale: the original problem was hunting orphans manually with 15+ traversal calls after the fact. Surfacing candidates at store time makes no-orphan the default path, not a cleanup chore. Suggestion only — never auto-creates edges (agent judges relevance).

### Edge-type suggestion (not blind `related`)

When suggesting, or when the agent passes `link_to` without `edge_type`, suggest the type instead of defaulting to `related`:

- Same topic, new record vs older one on the same decision → suggest `supersedes` / `evolves`.
- Negation/contradiction signals between the two contents → suggest `contradicts`.
- Otherwise → `related`.

Reuse the same content-comparison signals the similarity pass already produces. Suggestion only; the agent confirms. This stops the "everything is `related`" degradation where the graph loses its typed meaning.

---

## Edge Cases

| Case | Behavior |
|------|----------|
| Same content, different project | Allowed (hash check scoped to project) |
| >0.92 with force=true | Accepted, no warning |
| Archived memory similar to new | Still warn/block (archived not deleted, still searchable) |
| Superseded memory similar to new | Lower priority in comparison (active memories checked first) |
