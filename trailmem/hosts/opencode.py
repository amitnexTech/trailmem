"""OpenCode — detected but NOT auto-written: a hand-written entry corrupted
its config once (2026-07-19) and the attribution miss is still an open bug.
Schema per opencode.ai/config.json McpLocalConfig: key `mcp`, env key
`environment`. Flip write=True once re-verified against the live binary."""

import shutil

from . import _util
from ._util import Host


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


HOST = Host(
    "OpenCode", "opencode",
    detect=_detect,
    artifacts=[
        _util.json_mcp_artifact(_path, "mcp", _entry, write=False),
        _util.skill_artifact(lambda: _util._HOME() / ".config" / "opencode" / "skills"),
    ],
    mcp_entry=_entry,
)
