"""Antigravity CLI (agy) — verified host (live self-report 2026-07-23, agy 1.1.5).

MCP: standard mcpServers map at ~/.gemini/config/mcp_config.json, plain JSON,
{command, args, env} — the exact entry std_entry emits runs on the live host
and the MCP child receives the TRAILMEM_AGENT_TYPE pin, hence write=True.
No `agy mcp` CLI exists (config file is the only path); servers initialize at
startup — restart after changes.

Session identity: ANTIGRAVITY_CONVERSATION_ID is set in the host process env
and `conversationId` arrives top-level in hook/statusline stdin JSON, BUT the
MCP child gets NO ANTIGRAVITY_* vars (/proc environ dump: zero lines) — so
MCP calls run stateless; the id is only usable by host-spawned processes
(statusline). Payloads also carry `workspacePaths` — an ARRAY, unusable by
the first-string-wins payload scan, so project stays on the cwd default.

Hooks: PreToolUse/PostToolUse/PreInvocation/PostInvocation/Stop only — ALL
per turn; no SessionStart/SessionEnd exists. PreInvocation DOES inject stdout
into model context via {injectSteps:[{userMessage}]} — userMessage, NOT
ephemeralMessage: ephemeral is transient (one model invocation, unlogged) and
agy runs several invocations per turn, so an ephemeral briefing missed the
planner and evaporated by turn 2 (live-proven 2026-07-23). The welcome hook
maps injection to a session boundary via a dedup marker: `trailmem hook pre-invocation` injects the briefing only on a
conversation's FIRST fire (~/.trailmem/welcomed/<agent>-<id>), every later
fire emits {} — the no-per-turn-hooks rule is about per-turn CONTENT, which
the marker prevents. Hooks live as NAMED GROUPS in ~/.gemini/config/hooks.json
(one top-level key per group) — install/remove own exactly the "trailmem"
key, foreign groups untouched. The Codex-style tool-context transport IS
built (2026-07-23): agy dispatches every MCP call through call_mcp_tool
{ServerName, ToolName, Arguments} — shape verified from real brain
transcripts across 6+ conversations — and PreToolUse's officially documented
`overwrite` field does a SHALLOW top-level arg merge, so the hook echoes the
FULL Arguments object back with session_context added (trailmem calls only;
foreign servers get a bare {} no-op, never a decision). Transport LIVE-PROVEN
2026-07-23 (store landed with antigravity:<conversationId>, write_count
incremented), so the injected welcome is the SESSION-AWARE sessions.welcome —
it registers the row at conversation start and "saved N" tracking works.
No MCP-prompt slash commands and no custom command files → no /tm-save; the
other extra surface is the WORKSPACE usage skill (<cwd>/.agents/skills/ —
the sole non-builtin skills dir agy reads)."""

import json
import sys
from pathlib import Path

from . import _util
from ._util import Artifact, Host


def _detect():
    return ((_util._HOME() / ".antigravity-ide").exists()
            or (_util._HOME() / ".gemini" / "antigravity-cli").exists()
            or (_util._HOME() / ".gemini" / "antigravity-ide").exists())


def _path():
    return _util._HOME() / ".gemini" / "config" / "mcp_config.json"


def _settings_path():
    return _util._HOME() / ".gemini" / "antigravity-cli" / "settings.json"


def _entry(cmd, args):
    return _util.std_entry("antigravity", cmd, args)


# ---- hooks (~/.gemini/config/hooks.json, named group "trailmem") ----

def _hooks_path():
    return _util._HOME() / ".gemini" / "config" / "hooks.json"


def _hook_group() -> dict:
    # PreInvocation is FLAT (handler list), PreToolUse is GROUPED
    # (matcher + hooks wrapper) — per agy's official hooks.md.
    return {
        "PreInvocation": [{
            "type": "command",
            "command": f'"{sys.executable}" -m trailmem hook pre-invocation --agent antigravity',
            "timeout": 10,
        }],
        "PreToolUse": [{
            "matcher": "call_mcp_tool",
            "hooks": [{
                "type": "command",
                "command": f'"{sys.executable}" -m trailmem hook tool-context --agent antigravity',
                "timeout": 5,
            }],
        }],
    }


def install_hook() -> str:
    path = _hooks_path()
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            raise RuntimeError(
                f"{path} is not plain JSON — add the trailmem hook group "
                "manually")
        if not isinstance(data, dict):
            data = {}
    if data.get("trailmem") == _hook_group():
        return "hooks already installed"
    fresh = "trailmem" not in data
    data["trailmem"] = _hook_group()
    _util.write_json(path, data)
    verb = "written to" if fresh else "updated in"
    return (f"hooks (PreInvocation welcome once per conversation + PreToolUse "
            f"tool-context) {verb} {path} — restart agy")


def remove_hook() -> "str | None":
    path = _hooks_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        raise RuntimeError(
            f'{path} is not plain JSON — remove the "trailmem" hook group manually')
    if not isinstance(data, dict) or "trailmem" not in data:
        return None
    del data["trailmem"]
    _util.write_json(path, data)
    return f"removed hooks from {path}"


def _hook_check() -> str:
    try:
        group = json.loads(_hooks_path().read_text()).get("trailmem", {})
        pre = str(group.get("PreInvocation", [{}])[0].get("command", ""))
        tool = str(group.get("PreToolUse", [{}])[0]
                   .get("hooks", [{}])[0].get("command", ""))
        if "-m trailmem hook pre-invocation" in pre \
                and "-m trailmem hook tool-context" in tool:
            return "installed"
        if "-m trailmem hook pre-invocation" in pre:
            return "welcome only (pre-0.1.9) — run `trailmem integrate`"
        return "not installed"
    except Exception:
        return "not installed"


HOST = Host(
    "Antigravity", "antigravity",
    detect=_detect,
    artifacts=[
        _util.json_mcp_artifact(_path, "mcpServers", _entry, write=True),
        Artifact("hooks",
                 lambda cmd, args: install_hook(),
                 lambda: remove_hook(),
                 check=_hook_check),
        # Per-workspace (agy reads no user-level skills dir) — like Kiro's
        # workspace hook, re-run `trailmem integrate` in other workspaces.
        _util.skill_artifact(lambda: Path.cwd() / ".agents" / "skills"),
        _util.statusline_artifact(_settings_path, "antigravity"),
    ],
    mcp_entry=_entry,
    session_env=("ANTIGRAVITY_CONVERSATION_ID",),
    session_payload=("conversationId",),
)
