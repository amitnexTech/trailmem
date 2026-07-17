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

SERVER_NAME = "trailmem"


def mcp_command() -> str:
    """Absolute path of trailmem-mcp so hosts don't depend on the user's PATH."""
    found = shutil.which("trailmem-mcp")
    if found:
        return str(Path(found).resolve())
    sibling = Path(sys.argv[0]).resolve().parent / "trailmem-mcp"
    return str(sibling) if sibling.exists() else "trailmem-mcp"


def _backup(path: Path) -> None:
    bak = path.with_name(path.name + ".bak-trailmem")
    if path.exists() and not bak.exists():
        shutil.copy2(path, bak)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _backup(path)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _patch_json_map(path: Path, key: str, entry: dict) -> str:
    """Add SERVER_NAME under `key` in a JSON config file, preserving the rest."""
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
    if SERVER_NAME in servers:
        return "already registered"
    servers[SERVER_NAME] = entry
    _write_json(path, data)
    return f"wrote {path}"


# ---- hosts with non-JSON registration ----

def _claude_detect() -> bool:
    return shutil.which("claude") is not None


def _claude_integrate(cmd: str) -> str:
    already = subprocess.run(
        ["claude", "mcp", "get", SERVER_NAME], capture_output=True, timeout=15
    ).returncode == 0
    if already:
        return "already registered"
    result = subprocess.run(
        ["claude", "mcp", "add", SERVER_NAME, "--scope", "user", "--", cmd],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "`claude mcp add` failed")
    return "registered via `claude mcp add --scope user`"


def _codex_detect() -> bool:
    return (Path.home() / ".codex").is_dir()


def _codex_integrate(cmd: str) -> str:
    # Appending a new table is the only TOML edit the stdlib can do safely.
    path = Path.home() / ".codex" / "config.toml"
    text = path.read_text() if path.exists() else ""
    if f"[mcp_servers.{SERVER_NAME}]" in text:
        return "already registered"
    path.parent.mkdir(parents=True, exist_ok=True)
    _backup(path)
    entry = f'[mcp_servers.{SERVER_NAME}]\ncommand = "{cmd}"\nargs = []\n'
    prefix = text if not text or text.endswith("\n") else text + "\n"
    path.write_text(prefix + ("\n" if text else "") + entry)
    return f"wrote {path}"


# ---- hosts registered by patching a JSON config ----
# name -> (detect, config path, map key, entry factory)

_HOME = Path.home
_STD = lambda cmd: {"command": cmd, "args": []}  # noqa: E731 - table readability

JSON_HOSTS = [
    ("Kiro",
     lambda: (_HOME() / ".kiro").is_dir(),
     lambda: _HOME() / ".kiro" / "settings" / "mcp.json",
     "mcpServers", _STD),
    ("Kilo",
     lambda: (shutil.which("kilo") is not None
              or (_HOME() / ".kilo" / "bin" / "kilo").exists()
              or (_HOME() / ".config" / "kilo" / "kilo.jsonc").exists()),
     lambda: _HOME() / ".config" / "kilo" / "kilo.jsonc",
     "mcp", lambda cmd: {"type": "local", "command": [cmd]}),
    ("OpenCode",
     lambda: (shutil.which("opencode") is not None
              or (_HOME() / ".config" / "opencode").is_dir()
              or (_HOME() / ".opencode.json").exists()),
     lambda: (_HOME() / ".opencode.json"
              if (_HOME() / ".opencode.json").exists()
              and not (_HOME() / ".config" / "opencode" / "opencode.json").exists()
              else _HOME() / ".config" / "opencode" / "opencode.json"),
     "mcp", lambda cmd: {"type": "local", "command": [cmd], "enabled": True}),
    ("Antigravity",
     lambda: ((_HOME() / ".antigravity-ide").exists()
              or (_HOME() / ".gemini" / "antigravity-cli").exists()
              or (_HOME() / ".gemini" / "antigravity-ide").exists()),
     lambda: _HOME() / ".gemini" / "config" / "mcp_config.json",
     "mcpServers", _STD),
    ("Zed",
     lambda: (shutil.which("zed") is not None
              or (_HOME() / ".config" / "zed").is_dir()),
     lambda: _HOME() / ".config" / "zed" / "settings.json",
     "context_servers", lambda cmd: {"source": "custom", "command": cmd, "args": []}),
    ("Cursor",
     lambda: (_HOME() / ".cursor").is_dir(),
     lambda: _HOME() / ".cursor" / "mcp.json",
     "mcpServers", _STD),
    ("Windsurf",
     lambda: (_HOME() / ".codeium" / "windsurf").is_dir(),
     lambda: _HOME() / ".codeium" / "windsurf" / "mcp_config.json",
     "mcpServers", _STD),
]


def _hosts() -> list[tuple]:
    hosts = [
        ("Claude Code", _claude_detect, _claude_integrate),
        ("Codex", _codex_detect, _codex_integrate),
    ]
    for name, detect, path, key, entry in JSON_HOSTS:
        hosts.append((
            name, detect,
            lambda cmd, path=path, key=key, entry=entry:
                _patch_json_map(path(), key, entry(cmd)),
        ))
    return hosts


HOSTS = _hosts()


def run() -> int:
    cmd = mcp_command()
    found = [(name, fn) for name, detect, fn in HOSTS if detect()]
    if not found:
        print("No supported agent hosts detected "
              "(" + ", ".join(name for name, _, _ in HOSTS) + ").")
        print(f"Any MCP agent works manually: stdio server, command `{cmd}`, no args/env.")
        print("See the 'Any other MCP agent' section in the README for the config shape.")
        return 0
    print("Found: " + ", ".join(name for name, _ in found))
    print(f"MCP server command: {cmd}")
    if not sys.stdin.isatty():
        print("Refusing to modify configs without an interactive y/N confirmation.")
        return 1
    answer = input("Integrate trailmem with "
                   + ", ".join(name for name, _ in found) + "? [y/N] ")
    if answer.strip().lower() not in ("y", "yes"):
        print("No changes made.")
        return 0
    failures = 0
    for name, fn in found:
        try:
            print(f"  {name}: {fn(cmd)}")
        except Exception as exc:
            failures += 1
            print(f"  {name}: ✗ {exc}")
    print("Restart the agent(s) to pick up the new MCP server.")
    return 1 if failures else 0
