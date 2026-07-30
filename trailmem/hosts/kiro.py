"""Kiro — verified host (live self-report 2026-07-23, kiro-cli 2.10.0;
hook scope re-verified 2026-07-27 on kiro-cli 2.14.2).

MCP: standard mcpServers map at ~/.kiro/settings/mcp.json, but the schema is
STRICT and fails closed — an entry carrying ANY unrecognized key is silently
dropped from the effective server set (no error). Entries must stay exactly
{command, args, env}.

Session identity: the hook stdin payload sends "session_id" but always as an
empty string, no session env var exists in the host process, and the MCP
child gets a near-empty env (TRAILMEM_AGENT_TYPE only). So Kiro runs
stateless by design. A real conversationId exists only inside
~/.kiro/logs/<ts>/kiro.log — deliberately NOT mined (racy log scraping,
against the identity contract).

Hooks: SessionStart only (like Codex, no SessionEnd; "Stop" fires per turn —
a no-per-turn-hooks violation for a session-boundary hook). Kiro MERGES both
hook dirs: user-level ~/.kiro/hooks/<id>.json AND <workspace>/.kiro/hooks/,
each firing independently (proven 2026-07-27 on 2.14.2: two marker hooks, one
per scope, both appeared in the same fresh session). The earlier claim that
only the workspace dir executes (tee-capture, 2.10.0) is therefore wrong for
current Kiro. So the hook is installed ONCE at user level and covers every
workspace; installs delete a workspace copy, because with both present the
briefing is injected twice per session. Hooks are not hot-loaded; they
activate on the next session start.
"""

import json
import sys
from pathlib import Path

from . import _util
from ._util import Artifact, Host


def _path():
    return _util._HOME() / ".kiro" / "settings" / "mcp.json"


def _entry(cmd, args):
    return _util.std_entry("kiro", cmd, args)


# ---- SessionStart hook (~/.kiro/hooks/trailmem-session-start.json)
# Unlike Claude Code/Codex, Kiro has no single shared hooks registry file —
# each hook is its own file (v1 hook format: one JSON doc with a "hooks"
# list). Both the user-level and the workspace dir are executed and merged,
# so one user-level file covers every workspace; a workspace copy would make
# the briefing fire twice, hence install removes it.

def _hook_path():
    return _util._HOME() / ".kiro" / "hooks" / "trailmem-session-start.json"


def _workspace_hook_path():
    # ≤0.1.9 wrote here (when only workspace hooks were believed to run).
    return Path.cwd() / ".kiro" / "hooks" / "trailmem-session-start.json"


def _hook_doc() -> dict:
    return {
        "version": "v1",
        "hooks": [{
            "name": "Trailmem Session Start Briefing",
            "trigger": "SessionStart",
            "action": {
                "type": "command",
                "command": f'"{sys.executable}" -m trailmem hook session-start --agent kiro',
                "timeout": 15,
            },
        }],
    }


def _drop_workspace() -> str:
    stale = _workspace_hook_path()
    if not stale.exists():
        return ""
    stale.unlink()
    return (f"; removed workspace hook ({stale}) — both scopes fire, so it "
            "would inject the briefing twice")


def install_hook() -> str:
    path = _hook_path()
    doc = _hook_doc()
    stale_note = _drop_workspace()
    note = "; user-level — covers every Kiro workspace" + stale_note
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except json.JSONDecodeError:
            existing = None
        if existing == doc:
            return "SessionStart hook already installed" + stale_note
        _util.write_json(path, doc)
        return f"SessionStart hook updated at {path}" + note
    _util.write_json(path, doc)
    return f"SessionStart hook written to {path}" + note


def remove_hook() -> "str | None":
    removed = [p for p in (_hook_path(), _workspace_hook_path()) if p.exists()]
    for p in removed:
        p.unlink()
    if not removed:
        return None
    return "removed SessionStart hook (" + ", ".join(map(str, removed)) + ")"


HOST = Host(
    "Kiro", "kiro",
    detect=lambda: (_util._HOME() / ".kiro").is_dir(),
    artifacts=[
        _util.json_mcp_artifact(_path, "mcpServers", _entry, write=True),
        Artifact("SessionStart hook",
                 lambda cmd, args: install_hook(),
                 lambda: remove_hook(),
                 check=_util.file_check(_hook_path)),
    ],
    mcp_entry=_entry,
    # session_env / session_payload: defaults only. The verified payload key
    # is "session_id" (always empty today → stateless); KIRO_SESSION_ID and
    # conversationId-in-payload were guesses disproven by the 73-var env dump
    # and 5 tee-captured payloads.
)
