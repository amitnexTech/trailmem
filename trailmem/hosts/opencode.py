"""OpenCode — verified host (live self-report 2026-07-23, opencode 1.17.9).

MCP: ~/.config/opencode/opencode.json, top-level key `mcp`, `type: local`,
ONE combined command array, env key `environment` (`env` also accepted).
Format live-verified: the exact entry _entry() emits runs on the live host
and the MCP child receives the TRAILMEM_AGENT_TYPE pin — write=True as of
0.1.9 (the 2026-07-19 corruption came from pre-verification hand-written
guesses). A sibling opencode.jsonc may exist; JSONC-with-comments falls back
to the printed manual entry via patch_json_map. `opencode mcp add` CLI also
exists. Config is read at startup only — restart after changes.

Session identity: NONE reaches trailmem. No session env var exists (full env
dump checked); the conversation id (ses_...) lives only in
~/.local/share/opencode/opencode.db — deliberately NOT mined (racy DB
scraping, against the identity contract). MCP children inherit the FULL
parent env plus the config `environment` map. OpenCode runs stateless.

No shell-command hooks and no scriptable statusline. Lifecycle events exist
only as JS/TS plugins (~/.config/opencode/plugins/): session.created is a
real once-per-session event but a plugin CANNOT inject stdout into model
context (client-API only), and there is NO session-end event — so a welcome
plugin is deliberately not built; welcome stays LLM-driven via the usage
skill. MCP prompts are not surfaced (tools only); the save flow is a custom
slash command: a .md file in ~/.config/opencode/commands/ (PLURAL — verified
against real files) surfaces as /<filename> in the TUI.
"""

import shutil

from . import _util
from ._util import Artifact, Host


def _detect():
    return (shutil.which("opencode") is not None
            or (_util._HOME() / ".config" / "opencode").is_dir()
            or (_util._HOME() / ".opencode.json").exists())


def _path():
    old = _util._HOME() / ".opencode.json"
    new = _util._HOME() / ".config" / "opencode" / "opencode.json"
    return old if old.exists() and not new.exists() else new


def _entry(cmd, args):
    return {"type": "local", "command": [cmd, *args], "enabled": True,
            "environment": {"TRAILMEM_AGENT_TYPE": "opencode"}}


def _tm_save_path():
    return _util._HOME() / ".config" / "opencode" / "commands" / "tm-save.md"


HOST = Host(
    "OpenCode", "opencode",
    detect=_detect,
    artifacts=[
        _util.json_mcp_artifact(_path, "mcp", _entry, write=True),
        _util.skill_artifact(lambda: _util._HOME() / ".config" / "opencode" / "skills"),
        Artifact("/tm-save command",
                 lambda cmd, args: _util.install_packaged(
                     "commands/tm-save.md", _tm_save_path(), "/tm-save command"),
                 lambda: _util.remove_file(_tm_save_path(), "/tm-save command"),
                 check=_util.file_check(_tm_save_path)),
    ],
    mcp_entry=_entry,
    # session_env: none — no session var exists; ses_ id never leaves the DB.
)
