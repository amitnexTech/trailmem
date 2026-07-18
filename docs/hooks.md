# trailmem — Hooks Specification (Q14)

## Philosophy

Hooks are **conveniences, not load-bearing**. Every hook can fail or be absent and the system stays correct:
- Boundary safety comes from `started_at` (set lazily on first `trailmem_*` call) — not from any hook.
- Welcome is available manually (`trailmem_welcome` / `trailmem welcome`) — the hook just automates the first call.
- **No per-turn hooks.** No PreToolUse/PostToolUse/UserPromptSubmit injection. This is a hard rule — per-turn memory injection was OMEGA's single biggest token-burn source (hook blocks repeated on every prompt). trailmem injects context exactly once per session (welcome), everything else is on-demand.

## Hook Entry Point (CLI)

One hidden subcommand serves all hook events:

```bash
trailmem hook session-start [--agent <type>]   # prints welcome text to stdout
trailmem hook session-stop  [--agent <type>]   # updates sessions.last_seen_at, prints nothing
```

Rules for both:
- **Always exit 0.** A memory-system failure must never block or delay the agent's session. Errors go to stderr + `~/.trailmem/hooks.log`, stdout stays clean.
- **Fast by construction:** welcome needs no embedding model, and DB waits are bounded by `busy_timeout=3000`. The enforced cap is the HOST-side hook timeout (10s start / 5s stop in the registration below) — there is no separate internal timer. If the DB is unavailable, the error goes to `~/.trailmem/hooks.log` and the hook still exits 0.
- Agent identity: `--agent` flag > `TRAILMEM_AGENT_TYPE` env > auto-detect from env (`CLAUDE_CODE_SESSION_ID` present → claude, `KIRO_SESSION_ID` → kiro, …).
- Session id: from env (same detection). No session id in env → skip session registration, still print welcome (session-less mode, boundary untracked).

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

Other hosts (Kiro/Codex/OpenCode) register the same two commands in their own hook config with their agent name. Hosts with no hook system: agent calls `trailmem_welcome` manually per its steering rules — lazy fallback covers registration either way.

### Non-hook hosts — two portable surfaces (one per host, never both)

- **MCP `instructions` field.** The server declares a self-guarding instruction at initialize (`mcp_server._INSTRUCTIONS`): *if a briefing is not already in context, call `trailmem_welcome` once — never twice*. Hook-equipped hosts already have the briefing injected, so the conditional prevents a duplicate call (a second call would re-emit the full pinned section via the short form). Clients that honor `instructions` get the welcome nudge with zero per-host config.
- **Usage skill (`SKILL.md`).** `trailmem integrate` installs a lazy-loaded Agent Skill into each detected host's user-level skills dir (Claude `~/.claude/skills/`, Codex `~/.codex/skills/`, Kilo `~/.config/kilo/skills/`, OpenCode `~/.config/opencode/skills/`). Only its name+description (~40 tokens) sit in context; the body loads on demand and teaches tool semantics — omit `project` (cwd auto-fill), omit `agent_type` (env pin), event_type choice, dedup-is-not-an-error, linking, archive rules — so an agent never reads trailmem's source/schema to figure out a call. The skill covers usage depth, NOT the welcome trigger (probabilistic loading makes it unfit for that).

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
2. **`/tm-save` slash command (Claude Code convenience).** `trailmem integrate` also drops a native command file at `~/.claude/commands/tm-save.md`. Redundant with the MCP prompt on Claude Code, kept as a familiar one-token entry point.
3. **Plain text (works everywhere, including clients with no prompt support — e.g. Codex, aider).** The `trailmem_store` tool is a universal MCP primitive, so the user can always just type *“save this session to trailmem”* and the agent calls the tool. A slash command is only sugar over this — nothing is unavailable without it.
4. **DIY per-agent wiring.** A user whose agent supports custom slash commands can point one at the same instruction. Formats differ per agent — see the README “Saving a session” section for the correct file location/format per host (and the config-format landmines to avoid).

The `save_session` prompt body and the `tm-save.md` file carry the *same* instruction: extract this session's decisions/lessons/tasks and call `trailmem_store` (English, correct `event_type`, linked, dedup-aware, no filler).

### The reminders — so the user remembers to trigger a save

- **Welcome tip** — the full welcome briefing ends with a one-line reminder to save before exit. Universal across hosts.
- **`trailmem statusline`** — a CLI that reads `session_id` from stdin JSON (Claude Code) or env, counts `memories WHERE session_id = ?`, and prints a one-line status: `🧠 trailmem: N saved this session`, or an amber `⚠ trailmem: 0 saved · save before exit` when nothing has been stored yet. Read-only, always exits 0. Wire it into a host statusline; for hosts without one, run it standalone.
- **Next-session flag** — if a prior session (same agent) registered but stored **zero** memories, the next welcome opens with a loud `🛑 LAST SESSION SAVED 0 MEMORIES` line. The backup for a forgotten save.

None of these auto-generate memory content (that stays the agent's job — the anti-bloat rule holds); they only surface the gap and give the user/agent a one-command way to act on it.

## Failure Matrix

| Failure | Effect | Recovery |
|---|---|---|
| session-start hook missing/fails | No auto-briefing | Lazy fallback: first `trailmem_*` call registers session; agent can call welcome manually |
| session-stop hook missing/fails | `last_seen_at` slightly stale | None needed — boundary uses `started_at`; purge tolerance is 90 days |
| DB locked (concurrent agent) | WAL + BEGIN IMMEDIATE retry (3×, 100ms backoff), then exit 0 silently | Welcome available on manual retry |
| Model missing (embeddings) | Welcome unaffected (no embedding needed for welcome queries) | `trailmem doctor` flags it |
| No session id in env | Welcome prints, boundary untracked for this session | Fine for one-off CLI use |

## What trailmem Hooks Will NEVER Do

- Inject content on every prompt/tool-call (per-turn noise — OMEGA's mistake).
- Auto-store memories on any lifecycle event (junk factory — locked Q10).
- Block, delay, or fail the host session (always exit 0).
- Instruct the agent to relay marketing/upsell text (nagware — the reason this project exists).

---

## Related specs

- [[welcome]] — the exact briefing path SessionStart invokes; boundary + anti-bloat behavior.
- [[mcp]] — process/concurrency contracts (stdio, WAL, `BEGIN IMMEDIATE`) the hook shares.
- [[schema]] — `sessions` table the stop-hook updates.
- [[cli]] — `trailmem hook session-start/session-stop` entry the host registers.
