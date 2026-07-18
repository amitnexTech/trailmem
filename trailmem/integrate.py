"""Detect installed agent hosts and register trailmem's MCP server, with permission.

Detection is read-only. Nothing is written until the user answers the single
y/N prompt, and every touched config file gets a one-time ``.bak-trailmem``
backup first. Claude Code is registered through its own ``claude mcp add``
CLI; Codex gets a TOML table appended; every other host gets its JSON config
patched in place. A config that fails to parse as plain JSON (JSONC comments,
trailing commas) is never rewritten — the command reports the exact manual
entry instead of destroying user content.

Fully generic auto-detection is impossible: MCP has no host registry, and
every agent picks its own config path, key, and entry shape. New hosts are
one JSON_HOSTS line (or a detect/integrate pair for exotic formats).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from .console import sym

SERVER_NAME = "trailmem"


def mcp_command() -> tuple[str, list[str]]:
    """Server launch shape: current Python + `-u -m trailmem.mcp_server`.

    NEVER a generated `trailmem-mcp` launcher: Windows Smart App Control
    blocks per-install unsigned .exes (Event Viewer CodeIntegrity 3077), so a
    host-spawned server dies silently with no fallback. sys.executable is the
    venv python that has trailmem installed (uv tool / pipx / pip alike), and
    `-u` keeps stdio unbuffered for MCP framing."""
    return sys.executable, ["-u", "-m", "trailmem.mcp_server"]


def _uses_old_launcher(existing: dict) -> bool:
    """Pre-0.1.7 entries launch the removed trailmem-mcp script."""
    cmd = existing.get("command")
    parts = cmd if isinstance(cmd, list) else [cmd or ""]
    return any("trailmem-mcp" in str(p) for p in parts)


def _backup(path: Path) -> None:
    bak = path.with_name(path.name + ".bak-trailmem")
    if path.exists() and not bak.exists():
        shutil.copy2(path, bak)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _backup(path)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _patch_json_map(path: Path, key: str, entry: dict) -> str:
    """Add SERVER_NAME under `key` in a JSON config file, preserving the rest.
    An existing entry missing the agent-attribution env map gets it added —
    hosts spawn MCP servers with a clean env, so TRAILMEM_AGENT_TYPE in the
    config entry is the only reliable attribution path."""
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            raise RuntimeError(
                f"{path} is not plain JSON (comments/JSONC?) — add this under "
                f'"{key}" manually: "{SERVER_NAME}": {json.dumps(entry)}'
            )
        if not isinstance(data, dict):
            data = {}
    else:
        data = {}
    servers = data.setdefault(key, {})
    existing = servers.get(SERVER_NAME)
    if isinstance(existing, dict):
        if _uses_old_launcher(existing):
            # Rebuild from the new entry (stale keys like a leftover "args"
            # can be rejected by strict hosts — Kilo refuses unknown keys),
            # but keep any user-added env vars. The TRAILMEM_AGENT_TYPE pin
            # MUST survive this — without it every store hard-rejects.
            fresh = dict(entry)
            for k in ("env", "environment"):
                if k in existing:
                    fresh[k] = {**entry.get(k, {}), **existing[k]}
            servers[SERVER_NAME] = fresh
            _write_json(path, data)
            return "upgraded to python -m launch"
        # The env MAP existing is not enough — the PIN inside it is what
        # attribution needs (an entry with only user-custom vars still breaks
        # every store).
        envkey = next((k for k in ("env", "environment") if k in entry), None)
        have = existing.get(envkey) if envkey else None
        if envkey and not isinstance(have, dict):
            existing[envkey] = entry[envkey]
        elif envkey and "TRAILMEM_AGENT_TYPE" not in have:
            have["TRAILMEM_AGENT_TYPE"] = entry[envkey]["TRAILMEM_AGENT_TYPE"]
        else:
            return "already registered"
        _write_json(path, data)
        return "added agent-attribution env"
    servers[SERVER_NAME] = entry
    _write_json(path, data)
    return f"wrote {path}"


# ---- hosts with non-JSON registration ----

def _claude_detect() -> bool:
    return shutil.which("claude") is not None


def _claude_integrate(cmd: str, args: list[str]) -> str:
    # Claude detects via its own CLAUDECODE env, but the explicit env pin keeps
    # attribution correct even if a project-scope .mcp.json overrides this entry.
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


def _codex_detect() -> bool:
    return (Path.home() / ".codex").is_dir()


def _codex_install_prompt() -> str:
    """Codex has no MCP-prompt support; a custom prompt file gives it
    /prompts:trailmem-save (format verified against Codex CLI 0.144)."""
    from importlib import resources
    dest = Path.home() / ".codex" / "prompts" / "trailmem-save.md"
    body = resources.files("trailmem").joinpath("commands/tm-save.md").read_text()
    if dest.exists() and dest.read_text() == body:
        return "prompt already installed"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(body)
    return "installed /prompts:trailmem-save"


_CODEX_ENV_LINE = 'env = { TRAILMEM_AGENT_TYPE = "codex" }'


def _codex_integrate(cmd: str, args: list[str]) -> str:
    # Appending a new table (or rewriting OUR known-shape table) is the only
    # TOML edit the stdlib can do safely. Codex spawns MCP servers with a clean
    # env (verified live: no CODEX_THREAD_ID reaches the server), so the env
    # pin is required.
    path = Path.home() / ".codex" / "config.toml"
    text = path.read_text() if path.exists() else ""
    header = f"[mcp_servers.{SERVER_NAME}]"
    # TOML literal (single-quote) strings: no escape processing, so Windows
    # backslash paths (C:\...\python.exe) survive verbatim.
    args_toml = "[" + ", ".join(f"'{a}'" for a in args) + "]"
    entry = f"{header}\ncommand = '{cmd}'\nargs = {args_toml}\n{_CODEX_ENV_LINE}\n"
    if header in text:
        start = text.index(header)
        end = text.find("\n[", start + len(header))
        block = text[start: end if end != -1 else len(text)]
        if "trailmem-mcp" in block:
            # Old entry launches the removed trailmem-mcp script — rewrite the
            # whole (our-own, known-shape) table with the python -m launch.
            _backup(path)
            path.write_text(text[:start] + entry + (text[end:] if end != -1 else ""))
            reg = "upgraded to python -m launch"
        elif "TRAILMEM_AGENT_TYPE" in block:
            reg = "already registered"
        else:
            _backup(path)
            at = start + len(header)
            path.write_text(text[:at] + "\n" + _CODEX_ENV_LINE + text[at:])
            reg = "added agent-attribution env"
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        _backup(path)
        prefix = text if not text or text.endswith("\n") else text + "\n"
        path.write_text(prefix + ("\n" if text else "") + entry)
        reg = f"wrote {path}"
    return f"{reg}; {_codex_install_prompt()}"


# ---- hosts registered by patching a JSON config ----
# name -> (detect, config path, map key, entry factory)

_HOME = Path.home

# Every entry pins TRAILMEM_AGENT_TYPE: hosts spawn MCP servers with a clean
# env (no session vars reach the server process — verified live for Codex and
# Kilo), so the config-entry env map is the only reliable attribution path.
# Env-key naming per host is schema-verified: Kilo + OpenCode use
# "environment" (app.kilo.ai/config.json, opencode.ai/config.json
# McpLocalConfig); the mcpServers-shaped hosts and Zed use "env".
def _std(agent):
    return lambda cmd, args: {"command": cmd, "args": args,
                              "env": {"TRAILMEM_AGENT_TYPE": agent}}


JSON_HOSTS = [
    ("Kiro",
     lambda: (_HOME() / ".kiro").is_dir(),
     lambda: _HOME() / ".kiro" / "settings" / "mcp.json",
     "mcpServers", _std("kiro")),
    ("Kilo",
     lambda: (shutil.which("kilo") is not None
              or (_HOME() / ".kilo" / "bin" / "kilo").exists()
              or (_HOME() / ".config" / "kilo" / "kilo.jsonc").exists()),
     lambda: _HOME() / ".config" / "kilo" / "kilo.jsonc",
     "mcp", lambda cmd, args: {"type": "local", "command": [cmd, *args],
                               "environment": {"TRAILMEM_AGENT_TYPE": "kilo"}}),
    ("OpenCode",
     lambda: (shutil.which("opencode") is not None
              or (_HOME() / ".config" / "opencode").is_dir()
              or (_HOME() / ".opencode.json").exists()),
     lambda: (_HOME() / ".opencode.json"
              if (_HOME() / ".opencode.json").exists()
              and not (_HOME() / ".config" / "opencode" / "opencode.json").exists()
              else _HOME() / ".config" / "opencode" / "opencode.json"),
     "mcp", lambda cmd, args: {"type": "local", "command": [cmd, *args], "enabled": True,
                               "environment": {"TRAILMEM_AGENT_TYPE": "opencode"}}),
    ("Antigravity",
     lambda: ((_HOME() / ".antigravity-ide").exists()
              or (_HOME() / ".gemini" / "antigravity-cli").exists()
              or (_HOME() / ".gemini" / "antigravity-ide").exists()),
     lambda: _HOME() / ".gemini" / "config" / "mcp_config.json",
     "mcpServers", _std("antigravity")),
    ("Zed",
     lambda: (shutil.which("zed") is not None
              or (_HOME() / ".config" / "zed").is_dir()),
     lambda: _HOME() / ".config" / "zed" / "settings.json",
     "context_servers", lambda cmd, args: {"source": "custom", "command": cmd, "args": args,
                                           "env": {"TRAILMEM_AGENT_TYPE": "zed"}}),
    ("Cursor",
     lambda: (_HOME() / ".cursor").is_dir(),
     lambda: _HOME() / ".cursor" / "mcp.json",
     "mcpServers", _std("cursor")),
    ("Windsurf",
     lambda: (_HOME() / ".codeium" / "windsurf").is_dir(),
     lambda: _HOME() / ".codeium" / "windsurf" / "mcp_config.json",
     "mcpServers", _std("windsurf")),
]


# Hosts that read Agent Skills (SKILL.md) from a user-level directory. The
# skill is lazy-loaded (only name+description sit in context), so it teaches
# tool semantics without the agent reading trailmem's source or schema.
_SKILL_DIRS = {
    "Claude Code": lambda: _HOME() / ".claude" / "skills",
    "Codex": lambda: _HOME() / ".codex" / "skills",
    "Kilo": lambda: _HOME() / ".config" / "kilo" / "skills",
    "OpenCode": lambda: _HOME() / ".config" / "opencode" / "skills",
}


def _install_skill(host: str) -> str | None:
    dir_fn = _SKILL_DIRS.get(host)
    if dir_fn is None:
        return None
    from importlib import resources
    body = resources.files("trailmem").joinpath("skill/SKILL.md").read_text()
    dest = dir_fn() / SERVER_NAME / "SKILL.md"
    if dest.exists() and dest.read_text() == body:
        return "usage skill already installed"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(body)
    return f"usage skill installed at {dest}"


def _install_claude_command() -> str:
    """Copy the bundled /tm-save slash command into ~/.claude/commands/.
    Claude Code reads *.md command files from there; other hosts ignore it."""
    from importlib import resources
    dest_dir = Path.home() / ".claude" / "commands"
    dest = dest_dir / "tm-save.md"
    body = resources.files("trailmem").joinpath("commands/tm-save.md").read_text()
    if dest.exists() and dest.read_text() == body:
        return "already installed"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest.write_text(body)
    return f"installed {dest}"


def _hosts() -> list[tuple]:
    hosts = [
        ("Claude Code", _claude_detect, _claude_integrate),
        ("Codex", _codex_detect, _codex_integrate),
    ]
    for name, detect, path, key, entry in JSON_HOSTS:
        hosts.append((
            name, detect,
            lambda cmd, args, path=path, key=key, entry=entry:
                _patch_json_map(path(), key, entry(cmd, args)),
        ))
    return hosts


HOSTS = _hosts()


def run() -> int:
    cmd, args = mcp_command()
    launch = " ".join([cmd, *args])
    found = [(name, fn) for name, detect, fn in HOSTS if detect()]
    if not found:
        print("No supported agent hosts detected "
              "(" + ", ".join(name for name, _, _ in HOSTS) + ").")
        print(f"Any MCP agent works manually: stdio server, command `{launch}`, "
              "env TRAILMEM_AGENT_TYPE=<agent>.")
        print("See the 'Any other MCP agent' section in the README for the config shape.")
        return 0
    print("Found: " + ", ".join(name for name, _ in found))
    print(f"MCP server command: {launch}")
    if not sys.stdin.isatty():
        print("Refusing to modify configs without an interactive y/N confirmation.")
        return 1
    answer = input("Integrate trailmem with "
                   + ", ".join(name for name, _ in found) + "? [y/N] ")
    if answer.strip().lower() not in ("y", "yes"):
        print("No changes made.")
        return 0
    failures = 0
    bad = sym("✗", "[X]")
    for name, fn in found:
        try:
            print(f"  {name}: {fn(cmd, args)}")
        except Exception as exc:
            failures += 1
            print(f"  {name}: {bad} {exc}")
        try:
            skill = _install_skill(name)
            if skill:
                print(f"  {name}: {skill}")
        except Exception as exc:
            print(f"  {name}: usage skill {bad} {exc}")
    if any(name == "Claude Code" for name, _ in found):
        try:
            print(f"  Claude Code /tm-save command: {_install_claude_command()}")
        except Exception as exc:
            print(f"  Claude Code /tm-save command: {bad} {exc}")
    print("Restart the agent(s) to pick up the new MCP server.")
    return 1 if failures else 0
