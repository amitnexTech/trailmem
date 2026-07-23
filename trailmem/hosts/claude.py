"""Claude Code — verified host, gold-standard registration: its own
`claude mcp add` CLI writes the config, so the host owns its schema and we
never hand-edit settings.json for MCP. The explicit env pin keeps attribution
correct even if a project-scope .mcp.json overrides the user-scope entry.

Hooks: SessionStart + SessionEnd groups in ~/.claude/settings.json "hooks".
SessionStart MUST carry matcher "startup|clear" — without it the hook fires
on ALL sources (startup, resume, clear, compact) and re-injects the briefing
on every resume and every compaction (live-hit 2026-07-23 on the
hand-installed matcherless group this artifact replaces). resume/compact
continue a context that already holds the briefing; only startup/clear need
it. install/remove own only the groups whose command runs `trailmem hook`;
foreign hook groups in the same event arrays survive untouched."""

import json
import shutil
import subprocess
import sys

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


# ---- hooks (~/.claude/settings.json "hooks", one group per event) ----

def _hook_groups() -> dict:
    py = f'"{sys.executable}" -m trailmem hook'
    return {
        "SessionStart": {
            "matcher": "startup|clear",
            "hooks": [{"type": "command",
                       "command": f"{py} session-start --agent claude",
                       "timeout": 10}],
        },
        "SessionEnd": {
            "hooks": [{"type": "command",
                       "command": f"{py} session-stop --agent claude",
                       "timeout": 5}],
        },
    }


def _is_ours(group) -> bool:
    return isinstance(group, dict) and any(
        "trailmem hook" in h.get("command", "")
        for h in group.get("hooks", []) if isinstance(h, dict))


def _read_settings() -> dict:
    path = _settings_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        raise RuntimeError(
            f"{path} is not plain JSON — add the trailmem hooks manually")
    return data if isinstance(data, dict) else {}


def install_hook() -> str:
    data = _read_settings()
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        hooks = data["hooks"] = {}
    changed = False
    for event, group in _hook_groups().items():
        existing = hooks.get(event) if isinstance(hooks.get(event), list) else []
        merged = [g for g in existing if not _is_ours(g)] + [group]
        if merged != existing:
            hooks[event] = merged
            changed = True
    if not changed:
        return "hooks already installed"
    _util.write_json(_settings_path(), data)
    return ("hooks (SessionStart startup|clear + SessionEnd) written to "
            f"{_settings_path()} — restart Claude Code")


def remove_hook() -> "str | None":
    data = _read_settings()
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return None
    changed = False
    for event in list(hooks):
        if isinstance(hooks[event], list):
            kept = [g for g in hooks[event] if not _is_ours(g)]
            if kept != hooks[event]:
                hooks[event] = kept
                changed = True
            if not kept:
                del hooks[event]
    if not changed:
        return None
    if not hooks:
        del data["hooks"]
    _util.write_json(_settings_path(), data)
    return f"removed hooks from {_settings_path()}"


def _hook_check() -> str:
    try:
        starts = _read_settings()["hooks"]["SessionStart"]
        ours = [g for g in starts if _is_ours(g)]
        if not ours:
            return "not installed"
        if ours[0].get("matcher") == "startup|clear":
            return "installed"
        return "no matcher (re-injects on resume) — run `trailmem integrate`"
    except Exception:
        return "not installed"


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
        Artifact("hooks",
                 lambda cmd, args: install_hook(),
                 lambda: remove_hook(),
                 check=_hook_check),
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
