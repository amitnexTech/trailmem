"""Antigravity — detected but NOT auto-written (config format never verified
against the live binary). Flip write=True once verified."""

from . import _util
from ._util import Host


def _detect():
    return ((_util._HOME() / ".antigravity-ide").exists()
            or (_util._HOME() / ".gemini" / "antigravity-cli").exists()
            or (_util._HOME() / ".gemini" / "antigravity-ide").exists())


def _path():
    return _util._HOME() / ".gemini" / "config" / "mcp_config.json"


def _settings_path():
    # statusLine here is live-verified (Claude-Code-shaped stdin JSON) even
    # though the MCP config format is not — hence statusline writes, MCP doesn't.
    return _util._HOME() / ".gemini" / "antigravity-cli" / "settings.json"


def _entry(cmd, args):
    return _util.std_entry("antigravity", cmd, args)


HOST = Host(
    "Antigravity", "antigravity",
    detect=_detect,
    artifacts=[
        _util.json_mcp_artifact(_path, "mcpServers", _entry, write=False),
        _util.statusline_artifact(_settings_path, "antigravity"),
    ],
    mcp_entry=_entry,
    session_env=("ANTIGRAVITY_CONVERSATION_ID",),
)
