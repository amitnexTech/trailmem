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

## session-stop

1. `UPDATE sessions SET last_seen_at = now() WHERE session_id = ?`. Nothing else.
2. **No memory creation** (locked Q10 — no junk). No summary generation. No prompts.
3. Non-critical by design: if it never fires (crash, `/exit`, MCP down), the boundary for the next session is still correct via `started_at`.

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
