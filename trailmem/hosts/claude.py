"""Claude Code — verified host, gold-standard registration: its own
`claude mcp add` CLI writes the config, so the host owns its schema and we
never hand-edit settings.json. The explicit env pin keeps attribution correct
even if a project-scope .mcp.json overrides the user-scope entry."""

import shutil
import subprocess

from . import _util
from ._util import Artifact, Host, SERVER_NAME


def _mcp_install(cmd, args):
    got = subprocess.run(
        ["claude", "mcp", "get", SERVER_NAME], capture_output=True, text=True, timeout=15
    )
    upgraded = ""
    if got.returncode == 0:
        if "trailmem-mcp" not in got.stdout:
            return "already registered"
        # Old entry launches the removed trailmem-mcp script — re-register.
        subprocess.run(["claude", "mcp", "remove", "--scope", "user", SERVER_NAME],
                       capture_output=True, timeout=30)
        upgraded = " (upgraded to python -m launch)"
    result = subprocess.run(
        ["claude", "mcp", "add", SERVER_NAME, "--scope", "user",
         "-e", "TRAILMEM_AGENT_TYPE=claude", "--", cmd, *args],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "`claude mcp add` failed")
    return "registered via `claude mcp add --scope user`" + upgraded


def _mcp_check():
    if shutil.which("claude") is None:
        return "claude CLI not on PATH"
    got = subprocess.run(
        ["claude", "mcp", "get", SERVER_NAME], capture_output=True, text=True, timeout=15
    )
    if got.returncode != 0:
        return "not registered — run `trailmem integrate`"
    if "trailmem-mcp" in got.stdout:
        return "STALE launcher (trailmem-mcp) — run `trailmem integrate`"
    return "registered"


def _mcp_remove():
    if shutil.which("claude") is None:
        return None
    got = subprocess.run(
        ["claude", "mcp", "get", SERVER_NAME], capture_output=True, text=True, timeout=15
    )
    if got.returncode != 0:
        return None
    result = subprocess.run(
        ["claude", "mcp", "remove", "--scope", "user", SERVER_NAME],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "`claude mcp remove` failed")
    return "removed via `claude mcp remove --scope user`"


def _tm_save_path():
    return _util._HOME() / ".claude" / "commands" / "tm-save.md"


def _settings_path():
    return _util._HOME() / ".claude" / "settings.json"


# Lambdas resolve the module functions at call time so tests can monkeypatch
# _mcp_remove (no live `claude` CLI in a sandbox home).
HOST = Host(
    "Claude Code", "claude",
    detect=lambda: shutil.which("claude") is not None,
    artifacts=[
        Artifact("MCP registration",
                 lambda cmd, args: _mcp_install(cmd, args),
                 lambda: _mcp_remove(),
                 check=lambda: _mcp_check()),
        _util.skill_artifact(lambda: _util._HOME() / ".claude" / "skills"),
        Artifact("/tm-save command",
                 lambda cmd, args: _util.install_packaged(
                     "commands/tm-save.md", _tm_save_path(), "/tm-save command"),
                 lambda: _util.remove_file(_tm_save_path(), "/tm-save command"),
                 check=_util.file_check(_tm_save_path)),
        _util.statusline_artifact(_settings_path, "claude"),
    ],
    session_env=("CLAUDE_CODE_SESSION_ID",),
)
