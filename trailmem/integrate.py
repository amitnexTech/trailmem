"""Detect installed agent hosts and register trailmem's MCP server, with permission.

Detection is read-only. Nothing is written until the user answers the single
y/N prompt, and every touched config file gets a one-time ``.bak-trailmem``
backup first. Claude Code is registered through its own ``claude mcp add``
CLI; Kiro / Codex / OpenCode get their config file patched in place.
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


def _load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _backup(path)
    path.write_text(json.dumps(data, indent=2) + "\n")


# ---- per-host detect / integrate ----

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


def _kiro_detect() -> bool:
    return (Path.home() / ".kiro").is_dir()


def _kiro_integrate(cmd: str) -> str:
    path = Path.home() / ".kiro" / "settings" / "mcp.json"
    data = _load_json(path)
    servers = data.setdefault("mcpServers", {})
    if SERVER_NAME in servers:
        return "already registered"
    servers[SERVER_NAME] = {"command": cmd, "args": []}
    _write_json(path, data)
    return f"wrote {path}"


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


def _kilo_detect() -> bool:
    return (shutil.which("kilo") is not None
            or (Path.home() / ".kilo" / "bin" / "kilo").exists()
            or (Path.home() / ".config" / "kilo" / "kilo.jsonc").exists())


def _kilo_integrate(cmd: str) -> str:
    # kilo.jsonc allows comments; never rewrite (and lose) a commented file.
    path = Path.home() / ".config" / "kilo" / "kilo.jsonc"
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            raise RuntimeError(
                f"{path} contains comments/JSONC — add trailmem under \"mcpServers\" manually: "
                f'{{"command": "{cmd}", "args": []}}'
            )
        if not isinstance(data, dict):
            data = {}
    else:
        data = {}
    servers = data.setdefault("mcpServers", {})
    if SERVER_NAME in servers:
        return "already registered"
    servers[SERVER_NAME] = {"command": cmd, "args": []}
    _write_json(path, data)
    return f"wrote {path}"


def _opencode_detect() -> bool:
    return (shutil.which("opencode") is not None
            or (Path.home() / ".config" / "opencode").is_dir()
            or (Path.home() / ".opencode.json").exists())


def _opencode_integrate(cmd: str) -> str:
    xdg = Path.home() / ".config" / "opencode" / "opencode.json"
    legacy = Path.home() / ".opencode.json"
    path = legacy if legacy.exists() and not xdg.exists() else xdg
    data = _load_json(path)
    servers = data.setdefault("mcp", {})
    if SERVER_NAME in servers:
        return "already registered"
    servers[SERVER_NAME] = {"type": "local", "command": [cmd], "enabled": True}
    _write_json(path, data)
    return f"wrote {path}"


HOSTS = [
    ("Claude Code", _claude_detect, _claude_integrate),
    ("Kiro", _kiro_detect, _kiro_integrate),
    ("Codex", _codex_detect, _codex_integrate),
    ("Kilo", _kilo_detect, _kilo_integrate),
    ("OpenCode", _opencode_detect, _opencode_integrate),
]


def run() -> int:
    cmd = mcp_command()
    found = [(name, fn) for name, detect, fn in HOSTS if detect()]
    if not found:
        print("No supported agent hosts detected (Claude Code, Kiro, Codex, Kilo, OpenCode).")
        print(f"Manual: register `{cmd}` as an MCP server named '{SERVER_NAME}' in your host.")
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
