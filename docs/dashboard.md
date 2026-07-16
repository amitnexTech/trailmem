# trailmem — Dashboard Specification

**Status: built (2026-07-17), REVIEWED + HARDENED (2026-07-17, commit 4d862c6).** An initial `trailmem/dashboard.py` exists (authored by Amit, wired into cli.py). Audit + fresh-install functional test PASS: loopback-only bind, shared store/ops service layer (no direct SQLite writes), zero CDN/external assets, trigger-based revisioned SSE with Last-Event-ID replay + reset-on-gap, core validation on all write flows, no hard-delete in UI. Hardening added during review: Host/Origin loopback validation on every request (blocks DNS rebinding + cross-site CSRF), scope check on edge removal, clean port-in-use error. The dashboard is a first-party local feature, not a replacement for the six stdio MCP tools.

## Product Goal

`trailmem dashboard` should make a memory graph easy and comfortable to inspect, navigate, and maintain for long sessions. It must feel quiet while the data changes: a user reading a memory must never lose their scroll position, selected item, graph camera, layout, filter, or unfinished form because another agent stored a memory.

The existing Omega dashboard is only a UX reference. Its five-second full polling and graph rebuild are explicitly rejected.

## Boundary and Safety

```bash
trailmem dashboard
# Local UI: http://127.0.0.1:3800
```

- Bind to loopback only by default; no public network listener in v1.
- MCP remains **stdio-only**. The dashboard's loopback HTTP/SSE channel is an internal UI transport, not an HTTP MCP server or shared remote API.
- The dashboard calls Trailmem's shared application/service layer. It must **never** write SQLite rows directly or duplicate store/edit/archive/link rules.
- All dashboard writes use the same transactions, validation, model handling, deduplication, FTS/vector synchronization, archive rules, and orphan checks specified in [[schema]], [[dedup]], and [[evolution]].
- Ship required frontend assets locally. The dashboard must remain usable offline and must not load analytics, fonts, scripts, or graph libraries from third-party CDNs.

## Quiet Live-Update Contract

### Initial data and revisions

The first load receives a compact graph snapshot with a monotonic `revision`. Full content is fetched only for the selected memory. Each later write increases the revision after its database transaction commits.

The UI subscribes to a local SSE stream. The server emits small change events, never a periodic full snapshot:

```text
id: 418
event: memory.updated
data: {"revision":418,"node_id":"mem-a1b2c3d4","changed":["title","status"]}
```

Required event kinds:

| Event | Client action |
|---|---|
| `memory.created` | Add one node/list row without resetting the view. |
| `memory.updated` | Patch that memory's summary; refresh its inspector only when it is not being edited. |
| `memory.archived` | Patch status and dim the node; retain it in graph/search according to active filters. |
| `memory.deleted` | Remove the node and its connected edges; show a quiet notice if it was selected. |
| `edge.created` / `edge.deleted` | Patch only the affected edge counts, graph edge, and inspector links. |
| `stats.updated` | Patch health/stat counters. |
| `reset` | Request one fresh snapshot only when the client missed revisions or server history is unavailable. |

Reconnect uses `Last-Event-ID`/`since_revision`. If the server cannot supply a complete event gap, it emits `reset`; this is the only permitted automatic full reload.

### State that updates must preserve

On every ordinary patch, preserve:

- selected memory and inspector scroll position;
- search text, filters, sort, active mode, and list scroll position;
- graph zoom/pan camera and current in-browser node positions;
- open dialogs and unsaved drafts;
- keyboard focus and accessibility context.

A tiny non-blocking “Synced” indicator may update; no toast, animation, or focus change is allowed for routine changes. If another agent edits the memory currently open in an unsaved form, show a passive conflict banner with explicit **Reload** and **Keep editing** choices. Never overwrite the user's draft.

## Default Experience

### Desktop layout

Use a dense but breathable three-region workspace:

1. **Top bar** — product name, project scope, search, connection state, and clickable health counters.
2. **Graph canvas** — pan/zoom graph with readable labels only at useful zoom levels; visible type/status/edge meaning; keyboard-accessible selection.
3. **Inspector** — full selected-memory content, metadata, archive/supersession context, and all inbound/outbound relationships.

The inspector is the reading surface, not a cramped card list. Use comfortable line length, clear hierarchy, durable whitespace, selectable text, and obvious status contrast. Archived/superseded knowledge stays readable but visually subdued, never hidden by default.

### Navigation and discoverability

- Clicking a node selects it, centers it only when the user requests it, and opens its inspector.
- Every relationship in the inspector is a clickable chip showing direction, type, `#id`, title, and optional reason. Clicking one selects and reveals that linked memory immediately.
- Selecting a memory highlights its direct neighborhood without dimming the entire graph so heavily that labels become unreadable.
- Search supports title/content/ID/node ID; filters include type, status, agent, project/global scope, pinned, and orphan state.
- Health counters are actionable: clicking orphans, stale tasks, or contradictions applies the matching filter and explains the remediation path.
- List/search results always show `#id`, `node_id` (or copy affordance), type, status, agent, edge count, and a restrained preview.

### Graph behavior

- Node color encodes type; shape, border, or badge distinguishes pinned/constraint, project/global scope, and archived/superseded status. Do not rely on color alone.
- Edge styling differentiates relationship type and direction. Hover/focus reveals its reason/metadata.
- New nodes appear without restarting the force simulation or moving existing nodes. Changed nodes preserve coordinates.
- Provide an explicit **Re-layout graph** action with a confirmation/explanation. Automatic re-layout is prohibited.
- The graph is an exploration aid, not the only way to work: an accessible list/inspector route remains fully usable without it.

## Write Flows

All mutations are explicit, validated, and reversible where the core supports it.

- **Create:** requires title, English-oriented content check (soft warning), type, scope, attribution, and at least one meaningful link before final completion. The form surfaces duplicate/near-duplicate outcomes from [[dedup]] with links to the candidate record.
- **Edit:** uses the core edit path and identifies fields changed by another writer before submit.
- **Archive/supersede:** requires the reason and required relationship; ordinary UI does not offer hard delete.
- **Link/unlink:** provides type, direction explanation, reason, duplicate prevention, and an orphan warning before an unlink creates one.
- **Hard delete:** absent from normal dashboard UI; only the existing explicit CLI safety path may expose it.

## Health and Feedback

The dashboard must surface—not silently repair—data problems:

- orphan memories;
- stale open tasks;
- unresolved contradiction edges;
- model unavailable / FTS-only mode, including the loss of semantic search and near-duplicate detection;
- database or event-stream connectivity problems.

Errors are contextual and actionable. Successful routine saves update the affected UI in place; they do not trigger a full refresh.

## Accessibility and Visual Quality

- Support keyboard navigation, focus states, semantic controls, and screen-reader labels for graph/list/inspector actions.
- Meet readable contrast in both light and dark themes; honor reduced-motion preferences.
- Do not use attention-seeking animations. Motion is reserved for intentional navigation and must be subtle.
- Responsive layouts may collapse the inspector into a focused view, but must preserve full-content reading and linked-memory navigation.

## Implementation Gate and Open Decisions

Before implementation, explicitly confirm these items rather than silently treating them as locked:

1. **Position persistence:** v1 must preserve layout during a live session. Persisting node positions across browser restart (for example in local storage or a dedicated local table) is still an open choice.
2. **Timeline mode:** graph + list/inspector are required; a dedicated timeline mode is deferred until approved.
3. **Exact loopback API schema:** this document locks revision/SSE behavior and service-layer ownership, but endpoint names and payload fields need an implementation review against the final core model.
4. **Authentication policy for non-loopback use:** v1 is loopback-only. Any LAN/remote mode requires a separate security design and explicit approval.

## Acceptance Criteria

Before dashboard release, demonstrate that:

1. A second agent stores, edits, archives, links, and unlinks memories while a user reads another memory; the reader's state remains intact.
2. No normal event calls a full graph render, restarts the simulation, or resets the graph camera.
3. A dropped SSE connection catches up by revision or performs one controlled `reset` reload.
4. All writes pass the same core validations as CLI/MCP, including archive and orphan rules.
5. Every visible relationship is keyboard-accessible and clickable to its connected memory.
6. The dashboard works offline with no third-party runtime requests.
7. Graph-disabled or assistive-technology users can search, inspect, and maintain memories through the list/inspector workflow.
