# trailmem — Knowledge Evolution

## Principle

Never lose history. Every change to knowledge leaves a trail. Agent can trace why any decision was made, what was tried before, and what failed.

---

## Three Types of Changes

### 1. Edit (Minor Correction)

```
When: Typo fix, formatting, adding small clarification to SAME decision
What happens: Content updates in-place
Trail: updated_at set, content_hash recomputed, embedding regenerated
History: Previous version NOT preserved (minor change, not worth tracking)

Rules:
  ✅ Typo/grammar fix
  ✅ Adding clarification to same decision
  ✅ Formatting improvement
  ❌ NOT for changing the actual decision/approach
  ❌ NOT for adding new information that contradicts old
```

### 2. Supersede (Decision Changed)

```
When: Old approach replaced by new one. Old was wrong/outdated.
What happens: 
  - New memory created
  - Old memory status → 'superseded'
  - Edge: new --[supersedes]--> old
  - archive_reason set on old (WHY superseded, min 20 chars)

One-call flow:
  trailmem_store(
      title="QTcpSocket for aria2",
      content="...",
      supersedes=7,
      archive_reason="WebSocket connection drops under load"
  )
  → Creates new #12
  → Archives #7 (status='superseded', archive_reason set)
  → Edge: #12 --[supersedes]--> #7

Trail visible:
  Agent reads #12 → sees supersedes edge → reads #7 → 
  knows full history: what was tried, why it failed, what replaced it
```

### 3. Archive (Dropped, No Replacement)

```
When: Feature dropped, approach abandoned, no replacement exists
What happens:
  - Memory status → 'archived'
  - archive_reason set (WHY archived, min 20 chars)
  - At least 1 edge MUST exist (link to related active memory)

Rules:
  - archive_reason mandatory (min 20 chars) — explain WHY
  - Link mandatory — cannot archive without connecting to something
  - Prevents "orphaned archived knowledge" that nobody can find

Flow:
  trailmem_edit(
      id=13,
      status="archived",
      archive_reason="Security audit deprioritized, will redo after app work complete",
      link_to=16,
      edge_type="related"
  )
```

---

## Evolution Chain Example

```
Day 1: #7 "Use WebSocket for aria2" [active]
Day 3: #12 "Use QTcpSocket for aria2" [active] (supersedes #7)
Day 7: #18 "Use HTTP JSON-RPC for aria2" [active] (supersedes #12)

Graph:
  #18 (active) --supersedes--> #12 (superseded) --supersedes--> #7 (superseded)

Agent reads #18 → traverses chain:
  "WebSocket tried → failed (connection drops)
   QTcpSocket tried → failed (blocking issues)
   HTTP JSON-RPC → works (current)"
```

---

## Evolves vs Supersedes

| | supersedes | evolves |
|--|-----------|---------|
| Old memory was | WRONG / failed / dropped | Correct at the time |
| New memory is | Replacement (old invalid) | Refinement (old was okay, new is better) |
| Old status | `superseded` | Can stay `active` or `superseded` |
| Example | "WebSocket → QTcpSocket" (WebSocket failed) | "v1 spec → v2 spec" (v1 was fine, v2 adds features) |

---

## Search Behavior for Archived/Superseded

| Status | In Query Results? | Priority |
|--------|------------------|----------|
| active | Yes | Full weight (1.0x) |
| archived | Yes | Lower weight (0.5x) |
| superseded | Yes | Lower weight (0.5x) |

All statuses searchable — archived/superseded = "negative knowledge" (what NOT to do).

---

## Archive Rules (Enforced in App)

1. `archive_reason` mandatory — min 20 chars. WHY archived.
2. At least 1 edge MUST exist — link to replacement or related memory.
3. Cannot archive without both. Error returned if missing.
4. Archived memories remain searchable (lower priority).
5. Dashboard shows archived as greyed/dimmed with reason visible.
