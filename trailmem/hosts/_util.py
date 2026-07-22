"""Shared plumbing for host integration modules.

One host = one module in this package exposing ``HOST``. Every artifact pairs
install with remove, and `trailmem integrate` / `trailmem uninstall` iterate
the SAME registry — install and reversal can't drift apart (they used to be
two hand-synced lists; a forgotten mirror entry meant uninstall residue).

Write policy (2026-07-19 pivot, after hand-written entries corrupted Kilo and
OpenCode configs): auto-write a third-party config ONLY when its format is
verified against the live host. Unverified hosts stay detected (attribution
needs that) but get the exact manual entry printed via `manual_mcp` instead.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Callable, Mapping

from ..identity import SessionContext

SERVER_NAME = "trailmem"

_HOME = Path.home  # single indirection point — tests monkeypatch this


@dataclass
class Artifact:
    """One installable unit. install(cmd, args) returns a status message;
    remove() returns a message, or None when nothing of ours was present.
    check(), when set, returns a read-only one-line status for `doctor`."""
    label: str
    install: Callable[[str, list], str]
    remove: Callable[[], "str | None"]
    auto_writes_config: bool = False
    check: "Callable[[], str] | None" = None


@dataclass
class Host:
    name: str
    agent: str
    detect: Callable[[], bool]
    artifacts: list
    # Entry factory for JSON-config hosts — exposed so the env-pin invariant
    # stays testable and manual_mcp can print the exact entry.
    mcp_entry: "Callable[[str, list], dict] | None" = None
    session_env: tuple[str, ...] = ()
    session_payload: tuple[str, ...] = ("session_id",)
    project_payload: tuple[str, ...] = ("cwd",)

    def resolve_context(
        self,
        payload: dict | None = None,
        env: Mapping[str, str] = os.environ,
        *,
        event: str | None = None,
        session_id: str | None = None,
        project: str | None = None,
    ) -> SessionContext:
        """Translate this host's native fields into the canonical envelope."""
        payload = payload if isinstance(payload, dict) else {}
        native_session = session_id or _first(payload, self.session_payload)
        if not native_session:
            native_session = env.get("TRAILMEM_SESSION_ID") or _first(
                env, self.session_env)
        native_project = project
        if native_project is None and not env.get("TRAILMEM_PROJECT"):
            native_project = _first(payload, self.project_payload)
        return SessionContext.create(
            agent_type=self.agent,
            session_id=native_session,
            project=native_project,
            event=event,
            source=f"{self.agent}-adapter",
            env=env,
        )


def _first(values: Mapping, keys: tuple[str, ...]):
    for key in keys:
        value = values.get(key)
        if value:
            return str(value)
    return None


def backup(path: Path) -> None:
    bak = path.with_name(path.name + ".bak-trailmem")
    if path.exists() and not bak.exists():
        shutil.copy2(path, bak)


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup(path)
    path.write_text(json.dumps(data, indent=2) + "\n")


def std_entry(agent: str, cmd: str, args: list) -> dict:
    return {"command": cmd, "args": args, "env": {"TRAILMEM_AGENT_TYPE": agent}}


def uses_old_launcher(existing: dict) -> bool:
    """Pre-0.1.7 entries launch the removed trailmem-mcp script."""
    cmd = existing.get("command")
    parts = cmd if isinstance(cmd, list) else [cmd or ""]
    return any("trailmem-mcp" in str(p) for p in parts)


def patch_json_map(path: Path, key: str, entry: dict) -> str:
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
        if uses_old_launcher(existing):
            # Rebuild from the new entry (stale keys like a leftover "args"
            # can be rejected by strict hosts — Kilo refuses unknown keys),
            # but keep any user-added env vars. The TRAILMEM_AGENT_TYPE pin
            # MUST survive this — without it every store hard-rejects.
            fresh = dict(entry)
            for k in ("env", "environment"):
                if k in existing:
                    fresh[k] = {**entry.get(k, {}), **existing[k]}
            servers[SERVER_NAME] = fresh
            write_json(path, data)
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
        write_json(path, data)
        return "added agent-attribution env"
    servers[SERVER_NAME] = entry
    write_json(path, data)
    return f"wrote {path}"


def remove_json_map(path: Path, key: str) -> "str | None":
    """Drop SERVER_NAME from `key` in a JSON config. None = nothing of ours."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        raise RuntimeError(
            f'{path} is not plain JSON (comments/JSONC?) — remove the '
            f'"{SERVER_NAME}" entry under "{key}" manually'
        )
    servers = data.get(key)
    if not isinstance(servers, dict) or SERVER_NAME not in servers:
        return None
    del servers[SERVER_NAME]
    write_json(path, data)
    return f"removed entry from {path}"


def manual_mcp(path: Path, key: str, entry: dict) -> str:
    """Unverified-host policy: print the exact entry, never write the file."""
    if path.exists():
        try:
            servers = json.loads(path.read_text()).get(key)
            if isinstance(servers, dict) and SERVER_NAME in servers:
                return "already registered (left untouched — config format unverified)"
        except (json.JSONDecodeError, AttributeError):
            pass
    return (f"not auto-configured (config format unverified against the live host) — "
            f'add this under "{key}" in {path}: "{SERVER_NAME}": {json.dumps(entry)}')


def install_packaged(resource: str, dest: Path, label: str) -> str:
    """Copy a file bundled in the trailmem package to dest (our own file —
    always safe to write, unlike third-party configs)."""
    body = resources.files("trailmem").joinpath(resource).read_text()
    if dest.exists() and dest.read_text() == body:
        return f"{label} already installed"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(body)
    return f"installed {label} ({dest})"


def remove_file(path: Path, label: str) -> "str | None":
    if not path.exists():
        return None
    path.unlink()
    return f"removed {label} ({path})"


def install_skill(skills_dir: Path) -> str:
    dest = skills_dir / SERVER_NAME / "SKILL.md"
    return install_packaged("skill/SKILL.md", dest, "usage skill")


def remove_skill(skills_dir: Path) -> "str | None":
    dest = skills_dir / SERVER_NAME / "SKILL.md"
    if not dest.exists():
        return None
    dest.unlink()
    try:
        dest.parent.rmdir()  # only if empty — user files in there survive
    except OSError:
        pass
    return f"removed usage skill {dest}"


def check_json_mcp(path: Path, key: str) -> str:
    """Read-only drift check for a JSON-config MCP entry (doctor)."""
    if not path.exists():
        return "not registered"
    try:
        servers = json.loads(path.read_text()).get(key)
    except (json.JSONDecodeError, AttributeError):
        return "config unreadable (JSONC?) — check manually"
    existing = servers.get(SERVER_NAME) if isinstance(servers, dict) else None
    if not isinstance(existing, dict):
        return "not registered"
    if uses_old_launcher(existing):
        return "STALE launcher (trailmem-mcp) — run `trailmem integrate`"
    envmap = existing.get("env") or existing.get("environment")
    if not isinstance(envmap, dict) or "TRAILMEM_AGENT_TYPE" not in envmap:
        return "missing TRAILMEM_AGENT_TYPE pin — run `trailmem integrate`"
    return "registered"


def file_check(path_fn, present: str = "installed", absent: str = "not installed"):
    return lambda: present if path_fn().exists() else absent


def json_mcp_artifact(path_fn, key: str, entry_fn, write: bool) -> Artifact:
    if write:
        def ins(cmd, args):
            return patch_json_map(path_fn(), key, entry_fn(cmd, args))
    else:
        def ins(cmd, args):
            return manual_mcp(path_fn(), key, entry_fn(cmd, args))
    return Artifact(
        "MCP registration",
        ins,
        lambda: remove_json_map(path_fn(), key),
        auto_writes_config=write,
        check=lambda: check_json_mcp(path_fn(), key),
    )


def statusline_install(path: Path, agent: str, cmd: str) -> str:
    """Wire `statusLine` in a Claude-Code-shaped settings.json. Write only if
    absent — an existing user statusline is never clobbered; the statusline
    process lacks the MCP env pin, hence the explicit --agent flag."""
    mark = f"-m trailmem statusline --agent {agent}"
    line = f'"{cmd}" {mark}'
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            return (f"statusline not auto-configured — {path} is not plain JSON; "
                    f'set "statusLine" to {{"type": "command", "command": {line!r}}} manually')
        if not isinstance(data, dict):
            data = {}
    existing = data.get("statusLine")
    if isinstance(existing, dict):
        if mark in str(existing.get("command", "")):
            return "statusline already wired"
        return (f"existing statusline kept (never overwritten) — to add the trailmem "
                f"segment, pipe stdin into: {line}")
    data["statusLine"] = {"type": "command", "command": line}
    write_json(path, data)
    return f"statusline wired in {path}"


def statusline_remove(path: Path, agent: str) -> "str | None":
    mark = f"-m trailmem statusline --agent {agent}"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    sl = data.get("statusLine") if isinstance(data, dict) else None
    if not isinstance(sl, dict) or mark not in str(sl.get("command", "")):
        return None  # absent or not ours (user wrapper) — leave it
    del data["statusLine"]
    write_json(path, data)
    return f"removed statusline from {path}"


def check_statusline(path: Path, agent: str) -> str:
    if path.exists():
        try:
            sl = json.loads(path.read_text()).get("statusLine")
            if isinstance(sl, dict):
                if f"-m trailmem statusline --agent {agent}" in str(sl.get("command", "")):
                    return "wired"
                return "foreign statusline present (kept — chain manually)"
        except (json.JSONDecodeError, AttributeError):
            return "config unreadable (JSONC?) — check manually"
    return "not wired"


def statusline_artifact(path_fn, agent: str) -> Artifact:
    return Artifact("statusline",
                    lambda cmd, args: statusline_install(path_fn(), agent, cmd),
                    lambda: statusline_remove(path_fn(), agent),
                    auto_writes_config=True,
                    check=lambda: check_statusline(path_fn(), agent))


def skill_artifact(dir_fn) -> Artifact:
    return Artifact("usage skill",
                    lambda cmd, args: install_skill(dir_fn()),
                    lambda: remove_skill(dir_fn()),
                    check=file_check(lambda: dir_fn() / SERVER_NAME / "SKILL.md"))
