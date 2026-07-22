"""Codex — verified host. Four artifacts: MCP TOML table, the
/prompts:trailmem-save custom prompt (Codex has no MCP-prompt support),
the usage skill, and lifecycle hooks. SessionStart injects the welcome once;
a targeted PreToolUse hook silently carries Codex's authoritative session id
into TrailMem MCP calls. Codex has no SessionEnd event.

TOML editing is limited to appending/rewriting OUR known-shape table — the
only edit the stdlib can do safely (tomllib reads, nothing writes)."""

import json
import sys

from . import _util
from ._util import Artifact, Host, SERVER_NAME

ENV_LINE = 'env = { TRAILMEM_AGENT_TYPE = "codex" }'


def _toml_path():
    return _util._HOME() / ".codex" / "config.toml"


def _mcp_install(cmd, args):
    # Codex spawns MCP servers with a clean env (verified live: no
    # CODEX_THREAD_ID reaches the server), so the env pin is required.
    path = _toml_path()
    text = path.read_text() if path.exists() else ""
    header = f"[mcp_servers.{SERVER_NAME}]"
    # TOML literal (single-quote) strings: no escape processing, so Windows
    # backslash paths (C:\...\python.exe) survive verbatim.
    args_toml = "[" + ", ".join(f"'{a}'" for a in args) + "]"
    entry = f"{header}\ncommand = '{cmd}'\nargs = {args_toml}\n{ENV_LINE}\n"
    if header in text:
        start = text.index(header)
        end = text.find("\n[", start + len(header))
        block = text[start: end if end != -1 else len(text)]
        if "trailmem-mcp" in block:
            # Old entry launches the removed trailmem-mcp script — rewrite the
            # whole (our-own, known-shape) table with the python -m launch.
            _util.backup(path)
            path.write_text(text[:start] + entry + (text[end:] if end != -1 else ""))
            return "upgraded to python -m launch"
        if "TRAILMEM_AGENT_TYPE" in block:
            return "already registered"
        _util.backup(path)
        at = start + len(header)
        path.write_text(text[:at] + "\n" + ENV_LINE + text[at:])
        return "added agent-attribution env"
    path.parent.mkdir(parents=True, exist_ok=True)
    _util.backup(path)
    prefix = text if not text or text.endswith("\n") else text + "\n"
    path.write_text(prefix + ("\n" if text else "") + entry)
    return f"wrote {path}"


def _mcp_check():
    path = _toml_path()
    text = path.read_text() if path.exists() else ""
    header = f"[mcp_servers.{SERVER_NAME}]"
    if header not in text:
        return "not registered — run `trailmem integrate`"
    start = text.index(header)
    end = text.find("\n[", start + len(header))
    block = text[start: end if end != -1 else len(text)]
    if "trailmem-mcp" in block:
        return "STALE launcher (trailmem-mcp) — run `trailmem integrate`"
    if "TRAILMEM_AGENT_TYPE" not in block:
        return "missing TRAILMEM_AGENT_TYPE pin — run `trailmem integrate`"
    return "registered"


def _hooks_check():
    path = _hooks_path()
    try:
        return "installed" if "-m trailmem hook " in path.read_text() else "not installed"
    except OSError:
        return "not installed"


def _mcp_remove():
    path = _toml_path()
    if not path.exists():
        return None
    text = path.read_text()
    header = f"[mcp_servers.{SERVER_NAME}]"
    if header not in text:
        return None
    start = text.index(header)
    end = text.find("\n[", start + len(header))
    _util.backup(path)
    path.write_text(text[:start] + (text[end + 1:] if end != -1 else ""))
    return f"removed [mcp_servers.{SERVER_NAME}] table from {path}"


# ---- Hooks (~/.codex/hooks.json) ----

def _hooks_path():
    return _util._HOME() / ".codex" / "hooks.json"


def _session_start_entry() -> dict:
    # python -m launch for the same reason as mcp_command(); the exe path is
    # quoted because hosts run hook commands through a shell.
    return {"type": "command",
            "command": f'"{sys.executable}" -m trailmem hook session-start --agent codex',
            "timeout": 10, "statusMessage": "Loading trailmem briefing"}


def _tool_context_entry() -> dict:
    return {"type": "command",
            "command": f'"{sys.executable}" -m trailmem hook tool-context --agent codex',
            "timeout": 5}


def _is_trailmem_hook(hook: dict) -> bool:
    return "-m trailmem hook " in str(hook.get("command", ""))


def _upsert_hook(data: dict, event: str, matcher: str, entry: dict) -> bool:
    matchers = data.setdefault("hooks", {}).setdefault(event, [])
    changed = False
    found = False
    for group in matchers:
        hooks = group.get("hooks", [])
        ours = [h for h in hooks if _is_trailmem_hook(h)]
        if not ours:
            continue
        if not found:
            found = True
            if group.get("matcher") != matcher:
                group["matcher"] = matcher
                changed = True
            kept = [h for h in hooks if not _is_trailmem_hook(h)]
            if ours[0] != entry or len(ours) > 1:
                changed = True
            group["hooks"] = kept + [entry]
        else:
            group["hooks"] = [h for h in hooks if not _is_trailmem_hook(h)]
            changed = True
    matchers[:] = [group for group in matchers if group.get("hooks")]
    if not found:
        matchers.append({"matcher": matcher, "hooks": [entry]})
        changed = True
    return changed


def install_hook() -> str:
    path = _hooks_path()
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            raise RuntimeError(
                f"{path} is not plain JSON — add TrailMem's SessionStart and "
                "PreToolUse hooks manually")
        if not isinstance(data, dict):
            data = {}
    changed = _upsert_hook(
        data, "SessionStart", "startup|clear", _session_start_entry())
    changed |= _upsert_hook(
        data, "PreToolUse", r"^mcp__trailmem__trailmem_.*$",
        _tool_context_entry())
    if not changed:
        return "Codex hooks already installed"
    _util.write_json(path, data)
    return f"Codex hooks written to {path} — restart Codex and trust them via /hooks"


def remove_hook():
    path = _hooks_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        raise RuntimeError(
            f"{path} is not plain JSON — remove the TrailMem hooks manually")
    removed = 0
    hooks_map = data.get("hooks", {})
    for event in ("SessionStart", "PreToolUse"):
        matchers = hooks_map.get(event)
        if not isinstance(matchers, list):
            continue
        before = sum(len(group.get("hooks", [])) for group in matchers)
        for group in matchers:
            group["hooks"] = [
                hook for hook in group.get("hooks", [])
                if not _is_trailmem_hook(hook)
            ]
        kept = [group for group in matchers if group.get("hooks")]
        removed += before - sum(len(group["hooks"]) for group in kept)
        if kept:
            hooks_map[event] = kept
        else:
            hooks_map.pop(event, None)
    if not removed:
        return None
    _util.write_json(path, data)
    return f"removed {removed} TrailMem hook(s) from {path}"


def _prompt_path():
    return _util._HOME() / ".codex" / "prompts" / "trailmem-save.md"


HOST = Host(
    "Codex", "codex",
    detect=lambda: (_util._HOME() / ".codex").is_dir(),
    artifacts=[
        Artifact("MCP registration",
                 lambda cmd, args: _mcp_install(cmd, args),
                 lambda: _mcp_remove(),
                 check=lambda: _mcp_check()),
        Artifact("save prompt",
                 lambda cmd, args: _util.install_packaged(
                     "commands/tm-save.md", _prompt_path(), "/prompts:trailmem-save"),
                 lambda: _util.remove_file(_prompt_path(), "/prompts:trailmem-save"),
                 check=_util.file_check(_prompt_path)),
        _util.skill_artifact(lambda: _util._HOME() / ".codex" / "skills"),
        Artifact("hooks",
                 lambda cmd, args: install_hook(),
                 lambda: remove_hook(),
                 check=lambda: _hooks_check()),
    ],
    session_env=("CODEX_THREAD_ID",),
)
