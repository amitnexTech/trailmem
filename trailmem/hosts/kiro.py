"""Kiro — verified host (live self-report 2026-07-23, kiro-cli 2.10.0).

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
a no-per-turn-hooks violation for a session-boundary hook). Kiro executes
ONLY <workspace>/.kiro/hooks/<id>.json — user-level ~/.kiro/hooks/ is dead
(proven via tee-capture across restarts: workspace fired, user-level never).
Hence the hook is a per-workspace artifact written to cwd, and installs clean
up the dead user-level file earlier releases wrote. Hooks are not hot-loaded;
they activate on the next session start.
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


# ---- SessionStart hook (<workspace>/.kiro/hooks/trailmem-session-start.json)
# Unlike Claude Code/Codex, Kiro has no single shared hooks registry file —
# each hook is its own file (v1 hook format: one JSON doc with a "hooks"
# list), and only the WORKSPACE hooks dir is executed. So this installs/
# removes ONE dedicated file under cwd, per workspace.

def _hook_path():
    return Path.cwd() / ".kiro" / "hooks" / "trailmem-session-start.json"


def _legacy_hook_path():
    # ≤0.1.8 wrote here; Kiro never executes user-level hooks — dead file.
    return _util._HOME() / ".kiro" / "hooks" / "trailmem-session-start.json"


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


def _drop_legacy() -> str:
    legacy = _legacy_hook_path()
    if not legacy.exists():
        return ""
    legacy.unlink()
    return f"; removed dead user-level hook ({legacy} — Kiro never runs it)"


def install_hook() -> str:
    path = _hook_path()
    doc = _hook_doc()
    legacy_note = _drop_legacy()
    note = ("; per-workspace — re-run `trailmem integrate` in other Kiro "
            "workspaces") + legacy_note
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except json.JSONDecodeError:
            existing = None
        if existing == doc:
            return "SessionStart hook already installed" + legacy_note
        _util.write_json(path, doc)
        return f"SessionStart hook updated at {path}" + note
    _util.write_json(path, doc)
    return f"SessionStart hook written to {path}" + note


def remove_hook() -> "str | None":
    removed = [p for p in (_hook_path(), _legacy_hook_path()) if p.exists()]
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
        Artifact("SessionStart hook (workspace)",
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
