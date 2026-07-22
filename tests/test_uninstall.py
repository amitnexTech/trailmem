"""`trailmem uninstall` invariants.

1. Removal is SURGICAL: only the trailmem key/table/files go; every other
   entry in a touched config survives, byte-meaning intact.
2. JSONC / unparseable configs are never rewritten — manual instruction only.
3. Default keeps ~/.trailmem (memories survive reinstall); --purge deletes it
   only after the typed 'purge' confirmation.
4. The package-removal command is printed, never executed.
"""

import builtins
import json
import os
import shutil
import sys
import tempfile
import tomllib
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HOME = os.path.join(tempfile.gettempdir(), "tm-uninstall-home")
shutil.rmtree(HOME, ignore_errors=True)
os.environ["TRAILMEM_HOME"] = f"{HOME}/.trailmem"
os.environ["TRAILMEM_DB"] = f"{HOME}/.trailmem/trailmem.db"


class _TtyStdin:
    def isatty(self):
        return True


def _fake_home() -> Path:
    """Populate HOME exactly as a full integrate run would have left it."""
    home = Path(HOME)
    shutil.rmtree(home, ignore_errors=True)
    entry = {"command": sys.executable, "args": ["-u", "-m", "trailmem.mcp_server"],
             "env": {"TRAILMEM_AGENT_TYPE": "kiro"}}
    kiro = home / ".kiro" / "settings" / "mcp.json"
    kiro.parent.mkdir(parents=True)
    kiro.write_text(json.dumps(
        {"mcpServers": {"trailmem": entry, "other-server": {"command": "keep-me"}}}))
    kiro_hook = home / ".kiro" / "hooks" / "trailmem-session-start.json"
    kiro_hook.parent.mkdir(parents=True)
    kiro_hook.write_text(json.dumps({
        "version": "v1",
        "hooks": [{"name": "Trailmem Session Start Briefing", "trigger": "SessionStart",
                  "action": {"type": "command",
                             "command": f'"{sys.executable}" -m trailmem hook session-start --agent kiro',
                             "timeout": 15}}]}))
    kilo = home / ".config" / "kilo" / "kilo.jsonc"
    kilo.parent.mkdir(parents=True)
    kilo.write_text(json.dumps({
        "theme": "dark",
        "mcp": {"trailmem": {"type": "local",
                             "command": [sys.executable, "-u", "-m", "trailmem.mcp_server"],
                             "environment": {"TRAILMEM_AGENT_TYPE": "kilo"}}}}))
    codex = home / ".codex" / "config.toml"
    codex.parent.mkdir(parents=True)
    codex.write_text(
        "model = 'gpt-5'\n\n[mcp_servers.trailmem]\ncommand = 'python'\n"
        "args = ['-u', '-m', 'trailmem.mcp_server']\n"
        'env = { TRAILMEM_AGENT_TYPE = "codex" }\n\n'
        "[mcp_servers.other]\ncommand = 'keep-me'\n")
    for d in (home / ".claude" / "skills", home / ".codex" / "skills"):
        (d / "trailmem").mkdir(parents=True)
        (d / "trailmem" / "SKILL.md").write_text("skill body")
    cmds = home / ".claude" / "commands"
    cmds.mkdir(parents=True)
    (cmds / "tm-save.md").write_text("save command")
    prompts = home / ".codex" / "prompts"
    prompts.mkdir(parents=True)
    (prompts / "trailmem-save.md").write_text("save prompt")
    (home / ".trailmem").mkdir(parents=True)
    (home / ".trailmem" / "trailmem.db").write_text("precious memories")
    return home


def run() -> None:
    from trailmem import integrate
    from trailmem.hosts import _util, claude

    home = _fake_home()
    real_home, real_stdin, real_input = _util._HOME, sys.stdin, builtins.input
    real_claude_remove = claude._mcp_remove
    _util._HOME = lambda: home
    # No live `claude` CLI in this sandbox home — helper is a no-op stand-in.
    claude._mcp_remove = lambda: None
    sys.stdin = _TtyStdin()
    try:
        # --- helper invariants first ---
        # JSONC config must never be rewritten
        jsonc = home / "broken.jsonc"
        jsonc.write_text('{"mcp": {/* comment */ "trailmem": {}}}')
        try:
            _util.remove_json_map(jsonc, "mcp")
            raise AssertionError("JSONC must raise, not rewrite")
        except RuntimeError as exc:
            assert "manually" in str(exc)
        assert "/* comment */" in jsonc.read_text(), "JSONC file must be untouched"
        jsonc.unlink()

        # nothing-of-ours cases return None
        assert _util.remove_json_map(home / "absent.json", "mcpServers") is None
        assert _util.remove_skill(home / ".config" / "kilo" / "skills") is None  # never created
        assert "uninstall" in integrate._package_removal_cmd()

        # --- full uninstall, default (no purge) ---
        builtins.input = lambda prompt="": "y"
        rc = integrate.uninstall(purge=False)
        assert rc == 0, rc

        kiro = json.loads((home / ".kiro" / "settings" / "mcp.json").read_text())
        assert "trailmem" not in kiro["mcpServers"]
        assert kiro["mcpServers"]["other-server"]["command"] == "keep-me", \
            "surgical removal must keep other servers"
        assert not (home / ".kiro" / "hooks" / "trailmem-session-start.json").exists(), \
            "SessionStart hook file must be removed"
        kilo = json.loads((home / ".config" / "kilo" / "kilo.jsonc").read_text())
        assert "trailmem" not in kilo["mcp"] and kilo["theme"] == "dark"
        codex = tomllib.loads((home / ".codex" / "config.toml").read_text())
        assert "trailmem" not in codex.get("mcp_servers", {})
        assert codex["mcp_servers"]["other"]["command"] == "keep-me"
        assert codex["model"] == "gpt-5"
        assert not (home / ".claude" / "skills" / "trailmem").exists()
        assert not (home / ".codex" / "skills" / "trailmem").exists()
        assert not (home / ".claude" / "commands" / "tm-save.md").exists()
        assert not (home / ".codex" / "prompts" / "trailmem-save.md").exists()
        assert (home / ".trailmem" / "trailmem.db").exists(), \
            "default uninstall must NEVER touch the memory DB"

        # second run: nothing left of ours → still exit 0, config untouched
        before = (home / ".kiro" / "settings" / "mcp.json").read_text()
        assert integrate.uninstall(purge=False) == 0
        assert (home / ".kiro" / "settings" / "mcp.json").read_text() == before

        # --- purge: wrong confirmation keeps the DB ---
        answers = iter(["y", "not-purge"])
        builtins.input = lambda prompt="": next(answers)
        assert integrate.uninstall(purge=True) == 0
        assert (home / ".trailmem" / "trailmem.db").exists(), \
            "wrong purge confirmation must keep the DB"

        # --- purge: typed confirmation deletes ~/.trailmem ---
        answers = iter(["y", "purge"])
        builtins.input = lambda prompt="": next(answers)
        assert integrate.uninstall(purge=True) == 0
        assert not (home / ".trailmem").exists(), "--purge must delete the DB dir"
    finally:
        _util._HOME, sys.stdin, builtins.input = real_home, real_stdin, real_input
        claude._mcp_remove = real_claude_remove

    print("UNINSTALL OK")


if __name__ == "__main__":
    run()
