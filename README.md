# trailmem

[![PyPI](https://img.shields.io/pypi/v/trailmem)](https://pypi.org/project/trailmem/)
[![Python](https://img.shields.io/pypi/pyversions/trailmem)](https://pypi.org/project/trailmem/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**Persistent, local-first graph memory for AI coding agents.**

Trailmem gives agents durable cross-session memory without provider lock-in: a local SQLite knowledge graph, typed relationships, explicit knowledge evolution, and token-disciplined briefings. It is designed for multiple local agents—Claude, Kiro, Codex, OpenCode, Kilo, and Gemini—to share useful project knowledge without silently creating junk memories.

## Quick start

Same commands on Windows, macOS, and Linux.

### Install (recommended: `uv` — no Python needed)

`trailmem` is a command-line tool, so install it as one — this puts `trailmem` on your `PATH` in every terminal. The cleanest way is [`uv`](https://docs.astral.sh/uv/), a standalone binary that needs **no pre-installed Python** (it fetches one for you):

```bash
# 1. Install uv (standalone — does NOT require Python):
curl -LsSf https://astral.sh/uv/install.sh | sh          # Linux / macOS
# Windows (PowerShell):  powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# 2. Install trailmem (uv downloads a Python for it if you don't have one):
uv tool install trailmem
```

Already have Python and prefer [`pipx`](https://pipx.pypa.io/)? `pipx install trailmem` then `pipx ensurepath` works the same way (pipx needs an existing Python).

<details>
<summary>Plain <code>pip</code> (only inside a virtualenv or CI)</summary>

```bash
pip install trailmem
```

`pip install` drops the `trailmem` command into the current Python's `bin`/`Scripts` folder, which is often **not on your `PATH`** — a global `pip install --user` or a system Python will leave you with `zsh: command not found: trailmem` (and on Debian/Ubuntu, a PEP 668 "externally-managed" error). Use `uv`/`pipx` above unless you're deliberately working inside an activated virtualenv. If you already ran `pip install` and hit `command not found`, either activate the venv you installed into or run it as `python -m trailmem` — or just switch to `uv tool install trailmem`.

</details>

### Set up and register

```bash
trailmem setup          # creates ~/.trailmem/, inits DB, downloads the default embedding model (~130 MB, one time)
trailmem doctor         # health check

# Register the MCP server with your agent host(s):
trailmem integrate      # detects installed agent hosts, asks before writing any config
```

`trailmem integrate` auto-detects nine hosts: **Claude Code, Codex, Kiro, Kilo, OpenCode, Antigravity, Zed, Cursor, Windsurf**. It shows what it found, asks once (y/N), backs up every config it touches (`.bak-trailmem`), skips hosts that are already registered, and never rewrites a config it can't parse losslessly (JSONC with comments gets the manual entry printed instead). On Claude Code it also installs a `/tm-save` slash command. On hosts that read Agent Skills (Claude Code, Codex, Kilo, OpenCode) it installs a lazy-loaded `trailmem` usage skill so agents learn the tool semantics without reading source.

### Saving a session before you exit

An agent that forgets to record memory (or a hard `/exit`) can drop a session's context — a host end-of-session hook can't help, because it runs after the agent is gone and never sees the conversation. Only the live agent, mid-session, can capture. trailmem gives it a portable trigger plus reminders.

**Trigger a save** — use whichever your client supports (they all end in the same instruction: extract this session's decisions/lessons/tasks and call `trailmem_store`):

| How | Works in | Invoke |
|-----|----------|--------|
| **MCP prompt** `save_session` (zero-config, portable) | Any client that surfaces MCP prompts | Claude Code `/mcp__trailmem__save_session` · VS Code `/mcp.trailmem.save_session` · Cursor & Windsurf: slash/prompt list · Zed: text threads only |
| **`/tm-save`** command (installed by `integrate`) | Claude Code | `/tm-save` |
| **Plain text** (always works) | Every client — the `trailmem_store` tool is universal | Type *"save this session to trailmem"* |

Clients with no prompt support (e.g. **Codex**, **aider**) use the plain-text path — nothing is lost, the tool is always available. If your agent supports **custom slash commands**, you can point one at the same instruction yourself; formats differ per host, so check that agent's command-file docs (and avoid the config landmines below).

**Reminders** so you remember to trigger it:

- **Statusline** — `trailmem statusline` prints `🧠 trailmem: N saved this session`, or `⚠ 0 saved · save before exit` when nothing's captured yet. Reads `session_id` from stdin JSON (Claude Code) or `CLAUDE_CODE_SESSION_ID`/`KIRO_SESSION_ID` env; read-only, always exits 0. Wire it into your host's statusline, or run it standalone.
- **Welcome tip** — the briefing ends with a save reminder (shown by hosts that surface the session-start output, e.g. Codex, Kilo).
- **Next-session flag** — if the previous session stored nothing, the next welcome opens with a loud reminder.

> **Wiring an unlisted agent yourself?** MCP config formats are not uniform, and a wrong guess can break the agent's launch. Known landmines: **VS Code / Copilot** uses the key `servers` (not `mcpServers`); **Continue** and **Goose** use YAML (a JSON writer corrupts them); **aider** has no MCP support at all. Always follow the agent's own current docs. The one thing that works everywhere without any of this is the plain-text path above.

Prefer manual MCP registration? Each host has its own mechanism:

| Host | Manual registration |
|------|--------------------|
| Claude Code | `claude mcp add trailmem -- trailmem-mcp` |
| Codex | add an `[mcp_servers.trailmem]` table to `~/.codex/config.toml` |
| Kiro | add `trailmem` under `mcpServers` in `~/.kiro/settings/mcp.json` |
| Kilo | add `trailmem` under `mcp` in `~/.config/kilo/kilo.jsonc` as `{"type":"local","command":["trailmem-mcp"]}` (kilo 7.x format) |
| OpenCode | add `trailmem` under `mcp` in `~/.config/opencode/opencode.json` |
| Antigravity | add `trailmem` under `mcpServers` in `~/.gemini/config/mcp_config.json` |
| Zed | add `trailmem` under `context_servers` in `~/.config/zed/settings.json` |
| Cursor | add `trailmem` under `mcpServers` in `~/.cursor/mcp.json` |
| Windsurf | add `trailmem` under `mcpServers` in `~/.codeium/windsurf/mcp_config.json` |

### Any other MCP agent

Trailmem works with **any agent that speaks MCP** — Cursor, Windsurf, Cline, Zed, Gemini CLI, or anything newer. `trailmem integrate` only automates the hosts above; for everything else, register it yourself. You need exactly three facts:

1. **Transport:** stdio (no URL, no port, no HTTP).
2. **Command:** `trailmem-mcp` — no arguments, no environment variables required.
3. **Server name:** `trailmem` (any name works; tool names don't depend on it).

Most agents use a JSON block shaped like this (key name varies — `mcpServers`, `mcp`, `servers`):

```json
{
  "mcpServers": {
    "trailmem": {
      "command": "trailmem-mcp",
      "args": []
    }
  }
}
```

If the agent can't find the command, use the absolute path — print it with:

```bash
which trailmem-mcp        # Windows: where trailmem-mcp
```

Then restart the agent and check the wiring: the agent should see six `trailmem_*` tools, and calling `trailmem_welcome` should return a briefing. `trailmem doctor` verifies the database side.

### Updating

```bash
trailmem update   # checks PyPI, upgrades in place using however you installed it
```

`trailmem update` detects whether this copy was installed with uv / pipx / pip and runs the right upgrade command (uv-tool installs need `uv tool install trailmem@latest --force` — a bare `uv tool upgrade` is a no-op on a pinned tool, which `trailmem update` handles for you). Editable/dev installs are refused (upgrade via git). After upgrading, **restart your agents** so their MCP servers reload — a schema migration runs on first start of the new code, and a still-running old server must not keep writing.

Prefer to do it by hand:

```bash
uv tool install trailmem@latest --force   # if installed with uv
pipx upgrade trailmem                      # if installed with pipx
pip install --upgrade trailmem             # if installed with pip (inside the venv)
```

There is no in-app "update available" notice — trailmem sends no telemetry, by design. `trailmem update` only checks PyPI when you run it.

The agent then gets six tools: `trailmem_welcome` (once-per-session briefing), `trailmem_store`, `trailmem_query`, `trailmem_show`, `trailmem_edit`, `trailmem_link`. Everything is also available to humans via the `trailmem` CLI (`store`, `query`, `show`, `list`, `stats`, `link`, `archive`, ...).

Try it from the CLI (note: `content` is positional; `--agent user` for your own notes):

```bash
trailmem store --title "First note" --type lesson --agent user "Something worth remembering."
trailmem query "what did I note earlier"
trailmem list
trailmem help                # or: trailmem <command> --help
```

## Why

- **Local-first.** One SQLite file (`~/.trailmem/trailmem.db`), WAL mode, no cloud, no daemon. Embeddings run locally via ONNX (default: bge-small-en-v1.5, user-swappable with `trailmem model use`).
- **A graph, not a list.** Typed edges (`related`, `supersedes`, `evolves`, `contradicts`, `derived_from`), orphan warnings at store time, supersede chains instead of destructive overwrites.
- **Token discipline.** Context is injected exactly once per session (welcome, ~600–800 tokens). No per-turn injection, ever. Repeat welcomes return a short form.
- **No junk memories.** 4-band duplicate detection (exact hash reject → >0.92 block → 0.85–0.92 warn → accept), mandatory titles, hard-reject on unattributed stores, no auto-store lifecycle hooks.
- **No telemetry.** The server writes only what the user needs (e.g. a local `hooks.log` diagnostic); it never emits analytics — a deliberate anti-goal, not an oversight.

## Status

v0.1.0 is [live on PyPI](https://pypi.org/project/trailmem/). Core implemented and tested: schema, store/dedup, query/show, welcome, MCP server, CLI, hooks, model management, loopback dashboard, host integration. The design contract lives in [`docs/`](docs/index.md) — schema, welcome lifecycle, duplicate policy, evolution rules, CLI/MCP surfaces, hooks, seeding playbook, and the dashboard contract.

## License

MIT
