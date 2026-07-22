"""Kiro — verified host (format checked live 2026-07-18): standard
mcpServers map at ~/.kiro/settings/mcp.json. Two artifacts: MCP registration
and a SessionStart hook (start ONLY — like Codex, Kiro has no SessionEnd
event and its "Stop" trigger fires per agent turn, a no-per-turn-hooks
violation for a session-boundary hook)."""

import json
import sys

from . import _util
from ._util import Artifact, Host


def _path():
    return _util._HOME() / ".kiro" / "settings" / "mcp.json"


def _entry(cmd, args):
    return _util.std_entry("kiro", cmd, args)


# ---- SessionStart hook (~/.kiro/hooks/trailmem-session-start.json) ----
# Unlike Claude Code/Codex, Kiro has no single shared hooks registry file —
# each hook is its own file at .kiro/hooks/<id>.json (v2 hook format: one
# JSON doc with a "hooks" list). So this installs/removes ONE dedicated file
# rather than merging into a shared JSON map like _util.patch_json_map does.

def _hook_path():
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


def install_hook() -> str:
    path = _hook_path()
    doc = _hook_doc()
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except json.JSONDecodeError:
            existing = None
        if existing == doc:
            return "SessionStart hook already installed"
        _util.write_json(path, doc)
        return f"SessionStart hook updated at {path}"
    _util.write_json(path, doc)
    return f"SessionStart hook written to {path}"


def remove_hook() -> "str | None":
    path = _hook_path()
    if not path.exists():
        return None
    path.unlink()
    return f"removed SessionStart hook ({path})"


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
    session_env=("KIRO_SESSION_ID",),
    session_payload=("session_id", "sessionId", "conversationId", "conversation_id"),
)
