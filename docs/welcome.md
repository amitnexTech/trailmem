# trailmem — Welcome Briefing Design

Design of the session-start briefing: the seven welcome sections, session-boundary tracking, anti-bloat short/full logic, dedup across sections, and the token budget.

**Status:** REFERENCE

## Purpose

Session start pe agent ko context dena — kya critical rules hain, kya hua recently, kya pending hai. Minimal tokens, maximum awareness.

## Core Principle

```
Pinned/Constraint = Full dish (always full content, never miss)
Everything else = Menu card (title + ID, query for details)
Exception: "Since last session" me bhi constraint/pinned = full content
```

## Sessions Table (Boundary + Anti-Bloat)

```sql
CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    agent_type TEXT NOT NULL,
    project TEXT,
    started_at TEXT NOT NULL,       -- boundary marker (immutable after set)
    last_seen_at TEXT NOT NULL,     -- activity/purge tracking only (NOT for boundary)
    last_welcome_at TEXT,           -- anti-bloat: track when welcome last served
    write_count INTEGER NOT NULL DEFAULT 0,
    last_write_at TEXT
);
```

**Critical rules:**
- `started_at` = set ONCE on first `trailmem_*` call. NEVER updated after.
- `last_seen_at` = updated on welcome + stop-hook only. NOT every tool call.
- Session keys are `<agent>:<external-session-id>`.
- Host-native IDs are converted to a versioned `SessionContext` by one
  auto-discovered host adapter; welcome never interprets native env/payload keys.
- Boundary query filters the same agent + project, excludes current, and ignores legacy fake IDs.
- Session row created on FIRST `trailmem_*` call (lazy), not only on welcome.
- `INSERT ON CONFLICT(session_id) DO UPDATE SET last_seen_at` (started_at untouched).
- Stop-hook = non-critical. If missed, boundary still safe (started_at already exists).
- No authoritative ID means stateless welcome: no boundary, anti-bloat, or zero-save claims.
- Rows >90 days auto-purged in `trailmem maintain --apply`.



```
Step 1: FETCH boundary FIRST (before registering!)
        → SELECT MAX(started_at) FROM sessions 
          WHERE agent_type=? AND project IS ? AND session_id != current_session_id
        → current_session_id MUST be excluded
          
Step 2: READ prior welcome state + REGISTER session (ATOMIC — BEGIN IMMEDIATE transaction)
        → BEGIN IMMEDIATE;  (write-lock, prevents race with concurrent agents)
        → SELECT last_welcome_at FROM sessions WHERE session_id = current;
          (capture PRIOR value — may be NULL if first call, or timestamp if repeat)
        → INSERT INTO sessions ... ON CONFLICT(session_id) 
          DO UPDATE SET last_seen_at = now, last_welcome_at = now
          (started_at NEVER clobbered)
        → COMMIT;
        
        INVARIANT: Lazy session-INSERT (from other trailmem_* tools called before welcome)
        MUST NEVER set last_welcome_at. Only welcome sets it. This preserves Flag 2 logic.

Step 3: Anti-bloat decision (using PRIOR value from Step 2)
        → IF prior_last_welcome_at WAS NOT NULL → SHORT response (pinned+stats)
        → IF prior_last_welcome_at WAS NULL → FULL welcome (first time)

Step 4: Render sections (using boundary from step 1)
```

**Why order matters:** If you register first, then query "last session" — current session IS the last one. Boundary = now. "Since" section = always empty.

---

## Sections

### 📌 PINNED + CONSTRAINTS (Section 1)

```
Who: pinned=1 OR event_type='constraint'
Filter: status='active' AND (project=current OR project IS NULL)
Display: FULL CONTENT + #id [node_id]
Cap: NO LIMIT — show all. Warn if >10.
Order: pinned DESC, created_at DESC
```

**Auto-pin rule:** `event_type='constraint'` is effectively always pinned (shown in welcome full content regardless of pinned field value).

### 🔄 LAST ACTIVITY (Section 2)

```
Who: Most recent memory by ANY agent (not just current)
Filter: status='active' 
        AND event_type IN ('decision','lesson','error_pattern','task','session_summary','constraint')
        AND (project=current OR project IS NULL)
Display: FULL CONTENT (if significant type) or TITLE+200char preview (if user_preference/memory)
Limit: 1
Dedup: Skip if already in Section 1
```

### 🔄 YOUR LAST (Section 3)

```
Who: Most recent memory by THIS agent specifically
Filter: agent_type=current AND status='active' AND (project=current OR project IS NULL)
Display: FULL CONTENT
Limit: 1
Show: ONLY if different from Section 2 (otherwise skip — no repeat)
First-time fallback: If no previous by this agent → show one line
    "First session for [agent] on this project." and SKIP this section.
    (Do NOT render a last-5 list here — Section 4's first-time fallback
    already shows last 5 significant memories; duplicating it wastes tokens.)
```

### 🆕 SINCE LAST SESSION (Section 4)

```
Who: Memories created AFTER boundary (from Step 1)
Filter: (project=current OR project IS NULL) AND status='active'
Display: 
  - constraint/pinned entries → FULL CONTENT (critical = never title-only)
  - everything else → #id [node_id] [↔N] TITLE [agent, time_ago]
Dedup: Skip anything already shown in Sections 1-3
First-time fallback (boundary=NULL): 
  Last 5 significant-type memories (decision/lesson/error_pattern/task/session_summary)
  Any agent, any time. Excludes user_preference/memory (low-value for first context).
```

### ⏳ OPEN TASKS (Section 5)

```
Who: event_type='task' AND status='active'
Filter: (project=current OR project IS NULL)
Display: #id [node_id] [↔N] TITLE [agent, age]
Limit: ALL (no silent truncation)
Warning: if >5, append "Consider resolving older tasks"
Dedup: Skip if already shown
```

### ⚠️ ACTION NEEDED (Section 6)

```
Show ONLY IF:
  - orphan_count > 0 (memories with zero edges)
  - stale_task_count > 0 (tasks open >7 days)
  - contradicts edges exist (unresolved conflicts)

If nothing pending → skip entire section (no noise)
Format: "⚠ 2 orphans need linking, 1 task stale 9d, 1 contradiction unresolved"
```

### 📊 STATS (Section 7)

```
Format: "X memories | Y edges | Z orphans | Project: name (N project + M global)"
Always shown. One line.
```

---

## Token Budget

| Section | Typical | Worst Case |
|---------|---------|------------|
| Pinned (3-5 entries, full) | ~400 | ~800 (7+ pinned) |
| Last activity (1, full) | ~125 | ~250 (4000 char memory) |
| Your last (1, full) | ~125 | ~250 |
| Since last (5 titles) | ~100 | ~200 |
| Open tasks (3 titles) | ~45 | ~100 |
| Action needed | ~0-30 | ~30 |
| Stats | ~20 | ~20 |
| **TOTAL** | **~600-800** | **~1200** |

Compare: Omega vendor = 800-2000 tokens. Trail worst case ≈ Omega typical.

---

## Dedup Logic

```python
shown_ids = set()

for section in [pinned, last_activity, your_last, since, tasks]:
    for memory in section.results:
        if memory.id in shown_ids:
            continue  # Silent skip, no "already shown" notes
        render(memory)
        shown_ids.add(memory.id)
```

Higher section wins. No duplicate rendering. Clean output.

---

## Rules

- `access_count` NOT incremented on welcome (only explicit trailmem_query)
- Project-scoped: current_cwd + global (NULL) only. Other projects filtered out.
- IDs always shown: `#id [node_id] [↔N]` format on every memory everywhere
- Session reconnect: `started_at` never clobbered (ON CONFLICT updates last_seen_at only)

---

## Related

- [[schema]] — `sessions` table, boundary columns (`started_at`/`last_seen_at`/`last_welcome_at`), and pinned/constraint fields this briefing reads.
- [[hooks]] — SessionStart invokes this exact welcome path; anti-bloat state is shared.
- [[mcp]] — `trailmem_welcome` tool parameters and the short-vs-full contract.
- [[dedup]] — the `[↔N]` edge-count and orphan signals surfaced in sections.
