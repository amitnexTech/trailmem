# trailmem — Hooks Specification (Q14)

Session lifecycle integration with agent hosts: the `trailmem hook session-start/session-stop` commands, per-host registration, save-awareness surfaces, and the no-per-turn-hooks rule.

**Status:** REFERENCE

## Philosophy

Hooks are **conveniences, not load-bearing**. Every hook can fail or be absent and the system stays correct:
- Boundary safety comes from `started_at` (set lazily on first `trailmem_*` call) — not from any hook.
- Welcome is available manually (`trailmem_welcome` / `trailmem welcome`) — the hook just automates the first call.
- **No per-turn content injection.** Hook output never adds memory content on
  every prompt/tool call. A targeted, zero-context `PreToolUse` adapter is
  allowed only to carry an authoritative canonical `session_context` into
  TrailMem's own MCP arguments.

## Host Adapter Contract

Every native host mechanic lives in one auto-discovered
`trailmem/hosts/<host>.py` module: agent detection, native session env names,
hook payload keys, project extraction, and hook/config install/uninstall.
Core code receives only:

```json
{
  "schema_version": 1,
  "agent_type": "codex",
  "session_id": "thread-id",
  "project": "/abs/project",
  "event": "tool-context",
  "source": "codex-adapter"
}
```

Invariant: one host conversation produces one `SessionContext` and one
namespaced `<agent>:<external-id>` session row. A new verified host requires
one host module; the registry discovers it automatically.

## Hook Entry Point (CLI)

One hidden subcommand serves all hook events:

```bash
trailmem hook session-start [--agent <type>]   # prints welcome text to stdout
trailmem hook session-stop  [--agent <type>]   # updates sessions.last_seen_at, prints nothing
trailmem hook tool-context [--agent <type>]    # rewrites TrailMem MCP input, JSON only
```

Rules for all events:
- **Always exit 0.** A memory-system failure must never block or delay the agent's session. Errors go to stderr + `~/.trailmem/hooks.log`, stdout stays clean.
- **Fast by construction:** welcome needs no embedding model, and DB waits are bounded by `busy_timeout=3000`. The enforced cap is the HOST-side hook timeout (10s start / 5s stop in the registration below) — there is no separate internal timer. If the DB is unavailable, the error goes to `~/.trailmem/hooks.log` and the hook still exits 0.
- Agent identity: trusted hook `--agent` > `TRAILMEM_AGENT_TYPE`. Native env
  detection is adapter-owned, never a core fallback.
- **Native stdin is adapter input.** Each host module maps its verified payload
  keys to canonical agent/session/project fields. `TRAILMEM_PROJECT` remains an
  explicit project override. Garbage/absent stdin is ignored silently.
- Session id fallback: `TRAILMEM_SESSION_ID` or a verified host-specific env.
  No session id anywhere means stateless briefing and no session row.
  PID/CLI/adhoc pseudo-sessions are forbidden.

## session-start

1. Resolves agent_type + session_id + project (cwd).
2. Calls the same code path as `trailmem_welcome` (anti-bloat included — if MCP already served welcome this session, hook prints the SHORT form, not a duplicate full one; same `last_welcome_at` state, same BEGIN IMMEDIATE guard).
3. Prints briefing to stdout → host injects into agent context.

### Claude Code registration (settings.json)

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          { "type": "command", "command": "trailmem hook session-start --agent claude", "timeout": 10 }
        ]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [
          { "type": "command", "command": "trailmem hook session-stop --agent claude", "timeout": 5 }
        ]
      }
    ]
  }
}
```

### Codex registration (`$CODEX_HOME/hooks.json`, default `~/.codex`)

`trailmem integrate` writes/merges two narrow hooks. Codex's MCP child does not
receive `CODEX_THREAD_ID` (the 0.145.0 self-report's counter-probe spawned from
the delegated shell — which already carried the var — so it proves nothing about
the host's own spawn; the earlier live check saw a clean env), so `PreToolUse`
carries the event's authoritative canonical `session_context` into TrailMem MCP
calls through `updatedInput`. It emits no model-visible context.

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|clear",
        "hooks": [
          { "type": "command", "command": "\"<python>\" -m trailmem hook session-start --agent codex", "timeout": 10, "statusMessage": "Loading trailmem briefing" }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "^mcp__trailmem__trailmem_.*$",
        "hooks": [
          { "type": "command", "command": "\"<python>\" -m trailmem hook tool-context --agent codex", "timeout": 5 }
        ]
      }
    ]
  }
}
```

(`<python>` is `sys.executable` of the install — same rationale as the MCP launch shape: no unsigned per-install .exe, no PATH dependence.)

SessionStart's full source set is `startup|resume|clear|compact` (0.145.0
self-report, official docs). `resume` and `compact` are intentionally excluded
because they continue the same Codex session and must not inject another
welcome. `clear` remains because it starts a new conversation boundary. No stop
hook is installed: Codex `Stop` is turn-scoped, not session-scoped, and no
SessionEnd event exists. SessionStart stdout becomes model-visible developer
context; Codex itself persists large hook output to
`<temp_dir>/hook_outputs/<session_id>/<uuid>.txt` — finding the briefing there
is the host's own delivery mechanism at work (proof of injection), not TrailMem
writing files.

After integrate writes the hook, restart Codex and trust the new definition via `/hooks` (a changed hook hash re-prompts for review). `trailmem uninstall` removes only the trailmem entry from `hooks.json`, leaving foreign hooks intact.

Claude Code (codified 2026-07-23): integrate manages SessionStart + SessionEnd groups in `~/.claude/settings.json` "hooks" — SessionStart carries matcher `startup|clear`, because a matcherless group fires on ALL sources (startup, resume, clear, compact) and re-injects the briefing on every resume/compaction (live-hit on the hand-installed group the artifact replaced; resume/compact continue a context that already holds the briefing). install/remove own only groups whose command runs `trailmem hook`; foreign groups in the same event arrays survive, and a legacy matcherless trailmem group is upgraded in place. OpenCode has NO shell-hook system at all (verified live 2026-07-23): lifecycle events exist only as JS/TS plugins, session.created cannot inject stdout into model context (client-API only) and there is no session-end event — so no welcome hook is installed there; its welcome stays LLM-driven via the usage skill. Kiro (verified live 2026-07-23): one dedicated file per hook, and only `<workspace>/.kiro/hooks/` is executed — user-level `~/.kiro/hooks/` is dead (tee-capture across restarts: workspace fired, user-level never), so the trailmem hook is a per-workspace artifact and installs remove the dead user-level file older releases wrote. Kiro hooks are not hot-loaded (active next session start), and its `session_id` payload field is always empty → the hook runs the stateless welcome path. Antigravity (agy, verified live 2026-07-23) HAS a shell-hook system (`~/.gemini/config/hooks.json` global / `<workspace>/.agents/hooks.json`; hooks are NAMED GROUPS, one top-level key each) with real stdout context injection (`injectSteps`/`userMessage` — NOT `ephemeralMessage`: ephemeral is transient, visible to one model invocation only and never persisted, and agy runs several invocations per turn, so an ephemeral briefing missed the planner call and evaporated by turn 2; live-proven 2026-07-23, hence the injected text carries a "[trailmem session briefing — context only, no reply needed]" preamble), but no SessionStart/SessionEnd — its five events all fire per model call. integrate therefore installs a DEDUPED welcome hook (group key `trailmem`, PreInvocation → `trailmem hook pre-invocation --agent antigravity`): the command injects the briefing only on a conversation's FIRST fire (marker file `~/.trailmem/welcomed/<agent>-<conversationId>`, pruned after 30 days) and emits a bare `{}` on every later fire — the no-per-turn-hooks rule targets per-turn CONTENT, which the marker prevents; stdout is always one JSON object so the loop never breaks. The injected welcome is the SESSION-AWARE `welcome()` (flipped 2026-07-23 after the live proof below): the id never reaches the MCP child via env (clean-env proven via /proc), but the PreToolUse transport carries it into every trailmem call, so stores ARE attributed to `antigravity:<conversationId>` and registration at conversation start completes the `write_count`/"saved N" tracking. The Codex-style tool-context transport IS built (2026-07-23): agy dispatches every MCP call through one dispatcher tool `call_mcp_tool` with args `{ServerName, ToolName, Arguments}` (shape verified from real brain transcripts across 6+ conversations), and PreToolUse's officially documented `overwrite` field does a SHALLOW top-level merge into those args — so the `trailmem` group also carries a PreToolUse handler (matcher `call_mcp_tool` → `trailmem hook tool-context --agent antigravity`) that echoes the FULL `Arguments` object back with canonical `session_context` added, for `ServerName == "trailmem"` calls only; every other server (and any malformed payload) gets a bare `{}` no-op — the hook never emits a permission decision for tools that aren't ours (trailmem calls come back `decision: allow`). The transport is LIVE-PROVEN (2026-07-23): a real agy conversation stored a memory that landed with `session_id antigravity:<conversationId>` and the session row's `write_count` incremented — bare `{}` for foreign servers and `decision: allow` for trailmem calls both behaved as designed. Hook removal deletes only the `trailmem` group; foreign named groups survive. Restart agy after install (hooks read at startup). Hosts with no hook system: agent calls `trailmem_welcome` manually per its steering rules — lazy fallback covers registration either way.

### Non-hook hosts — two portable surfaces (one per host, never both)

- **MCP `instructions` field.** The server declares a self-guarding instruction at initialize (`mcp_server._INSTRUCTIONS`): *if a briefing is not already in context, call `trailmem_welcome` once — never twice*. Hook-equipped hosts already have the briefing injected, so the conditional prevents a duplicate call (a second call would re-emit the full pinned section via the short form). Clients that honor `instructions` get the welcome nudge with zero per-host config.
- **Usage skill (`SKILL.md`).** `trailmem integrate` installs a lazy-loaded Agent Skill into each detected host's skills dir (Claude `~/.claude/skills/`, Codex `~/.codex/skills/`, Kilo `~/.config/kilo/skills/`, OpenCode `~/.config/opencode/skills/`; Antigravity per-workspace `<cwd>/.agents/skills/` — the only non-builtin skills dir agy reads). Only its name+description (~40 tokens) sit in context; the body loads on demand and teaches tool semantics — omit `project` (cwd auto-fill), omit `agent_type` (env pin), event_type choice, dedup-is-not-an-error, linking, archive rules — so an agent never reads trailmem's source/schema to figure out a call. The skill covers usage depth, NOT the welcome trigger (probabilistic loading makes it unfit for that).

**Redundancy rule:** per host exactly ONE auto-welcome surface — hook where the host has one, the MCP `instructions` conditional everywhere else. Welcome text must enter context once per session, never twice.

## session-stop

1. `UPDATE sessions SET last_seen_at = now() WHERE session_id = ?`. Nothing else.
2. **No memory creation** (locked Q10 — no junk). No summary generation. No prompts.
3. Non-critical by design: if it never fires (crash, `/exit`, MCP down), the boundary for the next session is still correct via `started_at`.

## Save-awareness (the `/exit` gap)

A host end-of-session hook runs *after* the agent is gone and never sees the conversation, so it cannot capture memory. The only reliable capture point is the live agent, mid-session. Several complementary, LLM-free surfaces close the gap.

### The save trigger — how a user asks the agent to save

The capture instruction reaches the agent through whichever of these its client supports, in order of portability:

1. **MCP prompt `save_session` (portable, zero-config).** trailmem's MCP server exposes a `save_session` prompt. Every MCP client that surfaces prompts shows it to the user automatically — no per-agent files, no config edits, no crash risk (prompts are read-only protocol negotiation, they never touch a config file). Invocation differs per client:
   - **Claude Code** — `/mcp__trailmem__save_session`
   - **VS Code (Copilot agent mode)** — `/mcp.trailmem.save_session`
   - **Cursor** — surfaced in the slash-command list
   - **Windsurf (Cascade)** — surfaced as a prompt
   - **Zed** — text threads only (not agent threads)
2. **`/tm-save` slash command (Claude Code + Kilo + OpenCode convenience).** `trailmem integrate` also drops a native command file at `~/.claude/commands/tm-save.md`, and the same packaged file at `~/.config/kilo/command/tm-save.md` (Kilo "workflow" slash command — note the SINGULAR `command/` dir on the live install; TUI-only, Kilo surfaces no MCP prompts and headless `kilo run` ignores slash invocation) and `~/.config/opencode/commands/tm-save.md` (PLURAL `commands/` — OpenCode surfaces MCP tools, never MCP prompts). Redundant with the MCP prompt on Claude Code, kept as a familiar one-token entry point; on Kilo and OpenCode it is the only slash surface. Antigravity surfaces neither MCP prompts nor command files — no /tm-save there; the plain-text path is its only save trigger.
3. **Plain text (works everywhere, including clients with no prompt support — e.g. Codex, aider).** The `trailmem_store` tool is a universal MCP primitive, so the user can always just type *“save this session to trailmem”* and the agent calls the tool. A slash command is only sugar over this — nothing is unavailable without it.
4. **DIY per-agent wiring.** A user whose agent supports custom slash commands can point one at the same instruction. Formats differ per agent — see the README “Saving a session” section for the correct file location/format per host (and the config-format landmines to avoid).

The `save_session` prompt body and the `tm-save.md` file carry the *same* instruction: extract this session's decisions/lessons/tasks and call `trailmem_store` (English, correct `event_type`, linked, dedup-aware, no filler).

### The reminders — so the user remembers to trigger a save

- **Welcome tip** — the full welcome briefing ends with a one-line reminder to save before exit. Universal across hosts.
- **`trailmem statusline`** — reports `sessions.write_count`. Successful creates
  and changed/linked edits count; duplicates and no-op edits do not. Legacy
  unknown rows and session-less hosts produce no status.
- **Next-session flag** — only the immediately previous authoritative session
  for the same agent and project can trigger the zero-save warning. Legacy/PID
  rows and older zero sessions are ignored.

None of these auto-generate memory content (that stays the agent's job — the anti-bloat rule holds); they only surface the gap and give the user/agent a one-command way to act on it.

## Failure Matrix

| Failure | Effect | Recovery |
|---|---|---|
| session-start hook missing/fails | No auto-briefing | Lazy fallback: first `trailmem_*` call registers session; agent can call welcome manually |
| session-stop hook missing/fails | `last_seen_at` slightly stale | None needed — boundary uses `started_at`; purge tolerance is 90 days |
| DB locked (concurrent agent) | WAL + BEGIN IMMEDIATE retry (3×, 100ms backoff), then exit 0 silently | Welcome available on manual retry |
| Model missing (embeddings) | Welcome unaffected (no embedding needed for welcome queries) | `trailmem doctor` flags it |
| No authoritative session id | Stateless welcome; CRUD works; no boundary/save claims | Adapter must emit one, or set `TRAILMEM_SESSION_ID` / pass legacy MCP `session_id` |

## What trailmem Hooks Will NEVER Do

- Inject memory content on every prompt/tool-call.
- Auto-store memories on any lifecycle event (junk factory — locked Q10).
- Block, delay, or fail the host session (always exit 0).
- Instruct the agent to relay marketing/upsell text (nagware — the reason this project exists).

---

## Related

- [[host-integration]] — adapter implementation and verification guide.
- [[welcome]] — the exact briefing path SessionStart invokes; boundary + anti-bloat behavior.
- [[mcp]] — process/concurrency contracts (stdio, WAL, `BEGIN IMMEDIATE`) the hook shares.
- [[schema]] — `sessions` table the stop-hook updates.
- [[cli]] — `trailmem hook session-start/session-stop` entry the host registers.
