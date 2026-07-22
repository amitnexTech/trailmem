"""Detect installed agent hosts and register trailmem, with permission.

Host knowledge lives in trailmem/hosts/ — one module per host, each exposing
HOST with paired install/remove artifacts, so `integrate` and `uninstall`
iterate the SAME registry and cannot drift. Detection is read-only; nothing
is written until the user answers the single y/N prompt, and every touched
config gets a one-time ``.bak-trailmem`` backup first.

Write policy is narrow, detection wide (2026-07-19 pivot, after hand-written
entries corrupted Kilo and OpenCode configs): configs are auto-written only
for hosts whose module marks the format verified against the live binary.
Every other detected host gets the exact manual entry printed instead; its
removal path still exists so uninstall cleans up entries written by older
releases.
"""

from __future__ import annotations

import os
import shutil
import sys

from .console import sym
from .hosts import HOSTS
from .hosts._util import SERVER_NAME  # noqa: F401 — public re-export

def mcp_command() -> tuple[str, list[str]]:
    """Server launch shape: current Python + `-u -m trailmem.mcp_server`.

    NEVER a generated `trailmem-mcp` launcher: Windows Smart App Control
    blocks per-install unsigned .exes (Event Viewer CodeIntegrity 3077), so a
    host-spawned server dies silently with no fallback. sys.executable is the
    venv python that has trailmem installed (uv tool / pipx / pip alike), and
    `-u` keeps stdio unbuffered for MCP framing."""
    return sys.executable, ["-u", "-m", "trailmem.mcp_server"]


def run() -> int:
    cmd, args = mcp_command()
    launch = " ".join([cmd, *args])
    found = [h for h in HOSTS if h.detect()]
    if not found:
        print("No supported agent hosts detected "
              "(" + ", ".join(h.name for h in HOSTS) + ").")
        print(f"Any MCP agent works manually: stdio server, command `{launch}`, "
              "env TRAILMEM_AGENT_TYPE=<agent>.")
        print("See the 'Any other MCP agent' section in the README for the config shape.")
        return 0
    print("Found: " + ", ".join(h.name for h in found))
    print(f"MCP server command: {launch}")
    if not sys.stdin.isatty():
        print("Refusing to modify configs without an interactive y/N confirmation.")
        return 1
    answer = input("Integrate trailmem with "
                   + ", ".join(h.name for h in found) + "? [y/N] ")
    if answer.strip().lower() not in ("y", "yes"):
        print("No changes made.")
        return 0
    failures = 0
    bad = sym("✗", "[X]")
    for host in found:
        for art in host.artifacts:
            try:
                print(f"  {host.name}: {art.install(cmd, args)}")
            except Exception as exc:
                failures += 1
                print(f"  {host.name}: {bad} {art.label}: {exc}")
    print("Restart the agent(s) to pick up the new MCP server.")
    return 1 if failures else 0


# ---- uninstall ----
# Surgical reversal of everything integrate wrote: only the trailmem
# key/table/files are removed, the rest of every config stays byte-identical
# in meaning. The .bak-trailmem backups are deliberately NOT restored — the
# user may have edited configs (or re-run integrate) after they were taken.


def _package_removal_cmd() -> str:
    """The command that removes this installed copy. Printed, never run: a
    live process cannot reliably delete itself (open files on Windows)."""
    exe = os.path.realpath(sys.executable).replace("\\", "/")
    if "/uv/tools/" in exe:
        return "uv tool uninstall trailmem"
    if "/pipx/" in exe:
        return "pipx uninstall trailmem"
    return f"{sys.executable} -m pip uninstall trailmem"


def uninstall(purge: bool = False) -> int:
    from .config import TRAILMEM_HOME
    if not sys.stdin.isatty():
        print("Refusing to modify configs without an interactive y/N confirmation.")
        return 1
    what = "every detected agent config"
    if purge:
        what += f" AND permanently delete the memory DB at {TRAILMEM_HOME}"
    answer = input(f"Remove trailmem from {what}? [y/N] ")
    if answer.strip().lower() not in ("y", "yes"):
        print("No changes made.")
        return 0

    bad = sym("✗", "[X]")
    removed = failures = 0
    # ALL hosts, not just detected ones — older releases may have written to a
    # host that no longer detects (or whose writes are now manual-only).
    for host in HOSTS:
        for art in host.artifacts:
            try:
                msg = art.remove()
            except Exception as exc:
                failures += 1
                print(f"  {host.name}: {bad} {art.label}: {exc}")
                continue
            if msg:
                removed += 1
                print(f"  {host.name}: {msg}")

    print(f"Removed {removed} trailmem artifact(s) from host configs.")

    if purge:
        if TRAILMEM_HOME.exists():
            print(f"--purge PERMANENTLY deletes every memory at {TRAILMEM_HOME}. "
                  "This cannot be undone.")
            if input("Type 'purge' to confirm: ").strip() != "purge":
                print(f"Purge aborted — memories kept at {TRAILMEM_HOME}.")
            else:
                shutil.rmtree(TRAILMEM_HOME)
                print(f"Deleted {TRAILMEM_HOME} (all memories erased).")
        else:
            print(f"Nothing to purge — {TRAILMEM_HOME} does not exist.")
    else:
        print(f"Memories KEPT at {TRAILMEM_HOME} — nothing was deleted there. "
              "Reinstalling trailmem brings them all back automatically.")
        print(f"To erase them too: `trailmem uninstall --purge` (or `rm -rf {TRAILMEM_HOME}`).")

    print("To remove the package itself, run:")
    print(f"  {_package_removal_cmd()}")
    print("Restart the agent(s) so they drop the removed MCP server.")
    return 1 if failures else 0
