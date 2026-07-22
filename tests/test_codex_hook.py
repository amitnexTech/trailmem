"""Codex hook adapter invariants.

1. SessionStart reads Codex's authoritative session_id + cwd from stdin.
2. PreToolUse preserves tool args and injects one canonical SessionContext.
3. integrate writes/merges both hooks surgically and uninstall reverses ours.
"""

import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HOME = os.path.join(tempfile.gettempdir(), "tm-codex-hook-home")
REPO_ROOT = str(Path(__file__).resolve().parent.parent)
shutil.rmtree(HOME, ignore_errors=True)
os.environ["TRAILMEM_HOME"] = f"{HOME}/.trailmem"
os.environ["TRAILMEM_DB"] = f"{HOME}/.trailmem/trailmem.db"

from trailmem.cli import main  # noqa: E402
from trailmem.hosts import _util, codex  # noqa: E402
from trailmem.schema import connect, init_db  # noqa: E402


def run() -> None:
    # --- 1. stdin payload → session row ---
    payload = {"session_id": "codex-thread-abc", "cwd": "/tmp/some-project"}
    real_stdin = sys.stdin
    sys.stdin = io.StringIO(json.dumps(payload))
    try:
        assert main(["hook", "session-start", "--agent", "codex"]) == 0
    finally:
        sys.stdin = real_stdin
    conn = connect()
    init_db(conn)
    row = conn.execute("SELECT * FROM sessions WHERE session_id = ?",
                       ("codex:codex-thread-abc",)).fetchone()
    assert row is not None, "stdin session_id must register the session"
    assert row["agent_type"] == "codex" and row["project"] == "/tmp/some-project"
    conn.close()

    # PreToolUse must be silent except for its machine-readable rewrite.
    payload = {
        "session_id": "codex-thread-abc",
        "tool_name": "mcp__trailmem__trailmem_query",
        "tool_input": {"text": "session identity", "limit": 3},
    }
    stdout = io.StringIO()
    sys.stdin = io.StringIO(json.dumps(payload))
    try:
        from contextlib import redirect_stdout
        with redirect_stdout(stdout):
            assert main(["hook", "tool-context", "--agent", "codex"]) == 0
    finally:
        sys.stdin = real_stdin
    output = json.loads(stdout.getvalue())
    hook = output["hookSpecificOutput"]
    assert hook["hookEventName"] == "PreToolUse"
    assert hook["permissionDecision"] == "allow"
    updated = hook["updatedInput"]
    assert updated["text"] == "session identity" and updated["limit"] == 3
    assert "session_id" not in updated
    assert updated["session_context"] == {
        "schema_version": 1,
        "agent_type": "codex",
        "session_id": "codex-thread-abc",
        "project": REPO_ROOT,
        "event": "tool-context",
        "source": "codex-adapter",
    }

    # garbage stdin must never fail the host
    sys.stdin = io.StringIO("not json at all")
    try:
        assert main(["hook", "session-start", "--agent", "codex"]) == 0
    finally:
        sys.stdin = real_stdin

    # --- 2. hooks.json lifecycle ---
    real_home = _util._HOME
    _util._HOME = lambda: Path(HOME)
    try:
        path = Path(HOME) / ".codex" / "hooks.json"
        path.parent.mkdir(parents=True, exist_ok=True)

        msg = codex.install_hook()
        assert "written" in msg, msg
        data = json.loads(path.read_text())
        cmds = [h["command"] for m in data["hooks"]["SessionStart"] for h in m["hooks"]]
        assert any("trailmem hook session-start --agent codex" in c for c in cmds)
        assert data["hooks"]["SessionStart"][0]["matcher"] == "startup|clear"
        assert "resume" not in data["hooks"]["SessionStart"][0]["matcher"]
        tool_cmds = [h["command"] for m in data["hooks"]["PreToolUse"] for h in m["hooks"]]
        assert any("trailmem hook tool-context --agent codex" in c for c in tool_cmds)
        assert data["hooks"]["PreToolUse"][0]["matcher"] == \
            r"^mcp__trailmem__trailmem_.*$"
        assert "Stop" not in data["hooks"], "no per-turn Stop hook — hard rule"

        assert codex.install_hook() == "Codex hooks already installed"

        # stale command + old resume matcher → refreshed in place
        data["hooks"]["SessionStart"][0]["matcher"] = "startup|resume|clear"
        data["hooks"]["SessionStart"][0]["hooks"][0]["command"] = "/old/python -m trailmem hook session-start --agent codex"
        path.write_text(json.dumps(data))
        assert "written" in codex.install_hook()

        # foreign hook survives install + uninstall
        data = json.loads(path.read_text())
        data["hooks"]["SessionStart"].append(
            {"matcher": "startup", "hooks": [{"type": "command", "command": "graphify brief"}]})
        data["hooks"]["PreToolUse"].append(
            {"matcher": "^Bash$", "hooks": [{"type": "command", "command": "security scan"}]})
        path.write_text(json.dumps(data))
        assert codex.install_hook() == "Codex hooks already installed"
        assert codex.remove_hook() is not None
        data = json.loads(path.read_text())
        cmds = [h["command"] for m in data["hooks"]["SessionStart"] for h in m["hooks"]]
        assert cmds == ["graphify brief"], "only the trailmem hook may be removed"
        tool_cmds = [h["command"] for m in data["hooks"]["PreToolUse"] for h in m["hooks"]]
        assert tool_cmds == ["security scan"]
        assert codex.remove_hook() is None, "second removal is a no-op"
    finally:
        _util._HOME = real_home

    print("CODEX HOOK OK")


if __name__ == "__main__":
    run()
