"""Zed — detected but NOT auto-written: settings.json is user-owned JSONC
(comments are common), and the context_servers shape was never verified live.
Flip write=True once verified."""

import shutil

from . import _util
from ._util import Host


def _path():
    return _util._HOME() / ".config" / "zed" / "settings.json"


def _entry(cmd, args):
    return {"source": "custom", "command": cmd, "args": args,
            "env": {"TRAILMEM_AGENT_TYPE": "zed"}}


HOST = Host(
    "Zed", "zed",
    detect=lambda: (shutil.which("zed") is not None
                    or (_util._HOME() / ".config" / "zed").is_dir()),
    artifacts=[_util.json_mcp_artifact(_path, "context_servers", _entry, write=False)],
    mcp_entry=_entry,
)
