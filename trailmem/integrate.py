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
        missing = [k for k in ("env", "environment") if k in entry and k not in existing]
        if not missing:
            return "already registered"
        for k in missing:
            existing[k] = entry[k]
        _write_json(path, data)
        return "added agent-attribution env"
    servers[SERVER_NAME] = entry
    _write_json(path, data)
    return f"wrote {path}"


# ---- hosts with non-JSON registration ----

def _claude_detect() -> bool:
    return shutil.which("claude") is not None


def _claude_integrate(cmd: str) -> str:
    # Claude detects via its own CLAUDECODE env, but the explicit env pin keeps
    # attribution correct even if a project-scope .mcp.json overrides this entry.
    already = subprocess.run(
        ["claude", "mcp", "get", SERVER_NAME], capture_output=True, timeout=15
    ).returncode == 0
    if already:
        return "already registered"
    result = subprocess.run(
        ["claude", "mcp", "add", SERVER_NAME, "--scope", "user",
         "-e", "TRAILMEM_AGENT_TYPE=claude", "--", cmd],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "`claude mcp add` failed")
    return "registered via `claude mcp add --scope user`"


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


def _codex_integrate(cmd: str) -> str:
    # Appending a new table (or one line inside ours) is the only TOML edit the
    # stdlib can do safely. Codex spawns MCP servers with a clean env (verified
    # live: no CODEX_THREAD_ID reaches the server), so the env pin is required.
    path = Path.home() / ".codex" / "config.toml"
    text = path.read_text() if path.exists() else ""
    header = f"[mcp_servers.{SERVER_NAME}]"
    if header in text:
        start = text.index(header)
        end = text.find("\n[", start + len(header))
        block = text[start: end if end != -1 else len(text)]
        if "TRAILMEM_AGENT_TYPE" in block:
            reg = "already registered"
        else:
            _backup(path)
            at = start + len(header)
            path.write_text(text[:at] + "\n" + _CODEX_ENV_LINE + text[at:])
            reg = "added agent-attribution env"
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        _backup(path)
        entry = f'{header}\ncommand = "{cmd}"\nargs = []\n{_CODEX_ENV_LINE}\n'
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
    return lambda cmd: {"command": cmd, "args": [],
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
     "mcp", lambda cmd: {"type": "local", "command": [cmd],
                         "environment": {"TRAILMEM_AGENT_TYPE": "kilo"}}),
    ("OpenCode",
     lambda: (shutil.which("opencode") is not None
              or (_HOME() / ".config" / "opencode").is_dir()
              or (_HOME() / ".opencode.json").exists()),
     lambda: (_HOME() / ".opencode.json"
              if (_HOME() / ".opencode.json").exists()
              and not (_HOME() / ".config" / "opencode" / "opencode.json").exists()
              else _HOME() / ".config" / "opencode" / "opencode.json"),
     "mcp", lambda cmd: {"type": "local", "command": [cmd], "enabled": True,
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
     "context_servers", lambda cmd: {"source": "custom", "command": cmd, "args": [],
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
    if any(name == "Claude Code" for name, _ in found):
        try:
            print(f"  Claude Code /tm-save command: {_install_claude_command()}")
        except Exception as exc:
            print(f"  Claude Code /tm-save command: ✗ {exc}")
    print("Restart the agent(s) to pick up the new MCP server.")
    return 1 if failures else 0
