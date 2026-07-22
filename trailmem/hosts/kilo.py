"""Kilo — verified host (schema checked against the live 7.x binary after the
0.1.1 break: key `mcp`, `type: local`, ONE combined command array, env key
`environment`; strict — rejects unknown keys)."""

import shutil

from . import _util
from ._util import Host


def _detect():
    return (shutil.which("kilo") is not None
            or (_util._HOME() / ".kilo" / "bin" / "kilo").exists()
            or (_util._HOME() / ".config" / "kilo" / "kilo.jsonc").exists())


def _path():
    return _util._HOME() / ".config" / "kilo" / "kilo.jsonc"


def _entry(cmd, args):
    return {"type": "local", "command": [cmd, *args],
            "environment": {"TRAILMEM_AGENT_TYPE": "kilo"}}


HOST = Host(
    "Kilo", "kilo",
    detect=_detect,
    artifacts=[
        _util.json_mcp_artifact(_path, "mcp", _entry, write=True),
        _util.skill_artifact(lambda: _util._HOME() / ".config" / "kilo" / "skills"),
    ],
    mcp_entry=_entry,
    session_env=("KILO_RUN_ID",),
)
