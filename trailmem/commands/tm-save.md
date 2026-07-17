---
description: Save this session's durable memory to trailmem before you exit.
---

Capture the durable memory from THIS session into trailmem now, while the full
conversation is still in context. A host end-of-session hook cannot do this — it
runs after you are gone and never sees the conversation — so this is the reliable
capture point.

Do the following:

1. Review the conversation and identify what is worth persisting across sessions:
   - **decisions** — a rule, tool choice, structure, or enforced behavior we settled on
   - **lesson** — a bug, mistake, or non-obvious thing learned (include the *why*)
   - **task** — concrete follow-up work that is still open
   - **constraint / user_preference** — a durable rule or personal preference
   Skip ephemeral chatter, things already stored, and anything derivable from code
   or git history.

2. For each item, call `trailmem_store` with:
   - `content` in **English** (this is a hard rule even if we spoke another language),
   - the correct `event_type` from the list above,
   - a link to a related existing memory (`link_to` + `edge_type`) so it is not an
     orphan — query first if unsure what to link to.

3. Respect dedup: if `trailmem_store` reports a near-duplicate, update the existing
   memory with `trailmem_edit` instead of forcing a second copy.

4. If nothing this session is genuinely worth persisting, say so plainly
   ("nothing durable to save this session") — do NOT invent filler memories.

After saving, give a one-line summary of what you stored (ids + titles) so the user
can confirm before exiting.
