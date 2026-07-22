"""Cursor — detected but NOT auto-written (config format never verified
against the live binary). Flip write=True once verified."""

from . import _util
from ._util import Host


def _path():
    return _util._HOME() / ".cursor" / "mcp.json"


def _entry(cmd, args):
    return _util.std_entry("cursor", cmd, args)


HOST = Host(
    "Cursor", "cursor",
    detect=lambda: (_util._HOME() / ".cursor").is_dir(),
    artifacts=[_util.json_mcp_artifact(_path, "mcpServers", _entry, write=False)],
    mcp_entry=_entry,
)
