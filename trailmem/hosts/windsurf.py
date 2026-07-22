"""Windsurf — detected but NOT auto-written (config format never verified
against the live binary). Flip write=True once verified."""

from . import _util
from ._util import Host


def _path():
    return _util._HOME() / ".codeium" / "windsurf" / "mcp_config.json"


def _entry(cmd, args):
    return _util.std_entry("windsurf", cmd, args)


HOST = Host(
    "Windsurf", "windsurf",
    detect=lambda: (_util._HOME() / ".codeium" / "windsurf").is_dir(),
    artifacts=[_util.json_mcp_artifact(_path, "mcpServers", _entry, write=False)],
    mcp_entry=_entry,
)
