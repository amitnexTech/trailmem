"""Kiro hook adapter invariants (facts from the 2026-07-23 live self-report).

1. Kiro's real hook payload is {"session_id": "", "hook_event_name":
   "SessionStart", "cwd": ...} — session_id always EMPTY → must resolve
   stateless, never a fake session row. Guessed keys (conversationId,
   sessionId, KIRO_SESSION_ID env) were disproven and must NOT resolve.
2. MCP schema is strict/fails-closed: an entry with any unknown key is
   silently dropped by Kiro — our entry must stay exactly {command, args, env}.
3. integrate writes/removes <workspace>/.kiro/hooks/trailmem-session-start.json
   (Kiro only executes workspace hooks; user-level ~/.kiro/hooks/ is dead and
   any legacy file there gets cleaned up on install/remove).
"""

import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HOME = os.path.join(tempfile.gettempdir(), "tm-kiro-hook-home")
shutil.rmtree(HOME, ignore_errors=True)
os.environ["TRAILMEM_HOME"] = f"{HOME}/.trailmem"
os.environ["TRAILMEM_DB"] = f"{HOME}/.trailmem/trailmem.db"

from trailmem.cli import main  # noqa: E402
from trailmem.hosts import _util, kiro  # noqa: E402
from trailmem.schema import connect, init_db  # noqa: E402


def run() -> None:
    # --- 0. native payload mapping: only the verified key resolves ---
    assert kiro.HOST.resolve_context({"session_id": "a"}).session_id == "a"
    assert kiro.HOST.resolve_context({"session_id": ""}).session_id is None, \
        "Kiro's real payload (empty session_id) must resolve stateless"
    assert kiro.HOST.resolve_context(
        {"conversationId": "sess_b"}).session_id is None, \
        "conversationId lives only in kiro.log, never in a payload — no guessing"
    assert kiro.HOST.resolve_context({"sessionId": "c"}).session_id is None
    assert kiro.HOST.resolve_context(
        {}, env={"KIRO_SESSION_ID": "nope"}).session_id is None, \
        "no session env var exists on the host — disproven guess stays out"

    # strict fails-closed MCP schema: any extra key silently kills the entry
    entry = kiro.HOST.mcp_entry("py", ["-u", "-m", "trailmem.mcp_server"])
    assert set(entry) == {"command", "args", "env"}, entry

    # --- 1. stdin payload → hook exits 0; empty id never fakes a session ---
    payload = {"session_id": "", "hook_event_name": "SessionStart",
               "cwd": "/tmp/x"}  # verbatim real captured shape
    real_stdin = sys.stdin
    sys.stdin = io.StringIO(json.dumps(payload))
    try:
        assert main(["hook", "session-start", "--agent", "kiro"]) == 0
    finally:
        sys.stdin = real_stdin
    conn = connect()
    init_db(conn)
    assert not conn.execute(
        "SELECT session_id FROM sessions WHERE agent_type = 'kiro'"
    ).fetchall(), "empty session_id must stay stateless — no row at all"
    conn.close()

    # if Kiro ever wires a real id into session_id, it lands namespaced
    sys.stdin = io.StringIO(json.dumps(
        {"session_id": "sess_kiro-abc", "cwd": "/tmp/some-project"}))
    try:
        assert main(["hook", "session-start", "--agent", "kiro"]) == 0
    finally:
        sys.stdin = real_stdin
    conn = connect()
    init_db(conn)
    row = conn.execute("SELECT * FROM sessions WHERE session_id = ?",
                       ("kiro:sess_kiro-abc",)).fetchone()
    assert row is not None
    assert row["agent_type"] == "kiro" and row["project"] == "/tmp/some-project"
    conn.close()

    # garbage stdin must never fail the host
    sys.stdin = io.StringIO("not json at all")
    try:
        assert main(["hook", "session-start", "--agent", "kiro"]) == 0
    finally:
        sys.stdin = real_stdin

    # --- 2. hook FILE lifecycle: workspace-scoped + legacy cleanup ---
    real_home, real_cwd = _util._HOME, os.getcwd()
    workspace = Path(HOME) / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _util._HOME = lambda: Path(HOME)
    os.chdir(workspace)
    try:
        path = workspace / ".kiro" / "hooks" / "trailmem-session-start.json"
        legacy = Path(HOME) / ".kiro" / "hooks" / "trailmem-session-start.json"

        # a dead ≤0.1.8 user-level file gets cleaned up on install
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text("{}")

        msg = kiro.install_hook()
        assert "written" in msg, msg
        assert path.exists(), "hook must land in the WORKSPACE .kiro/hooks"
        assert not legacy.exists(), "dead user-level hook must be removed"
        data = json.loads(path.read_text())
        assert data["hooks"][0]["trigger"] == "SessionStart"
        assert "trailmem hook session-start --agent kiro" in data["hooks"][0]["action"]["command"]
        assert data["hooks"][0]["action"]["timeout"] > 0
        assert not any(h.get("trigger") == "Stop" for h in data["hooks"]), \
            "no per-turn Stop hook — same hard rule as Codex"

        assert "already installed" in kiro.install_hook()

        # stale/edited file → refreshed in place, not left as-is
        data["hooks"][0]["action"]["command"] = "stale command"
        path.write_text(json.dumps(data))
        assert "updated" in kiro.install_hook()

        legacy.write_text("{}")  # remove must clear both scopes
        msg = kiro.remove_hook()
        assert msg is not None and not path.exists() and not legacy.exists()
        assert kiro.remove_hook() is None, "second removal is a no-op"
    finally:
        _util._HOME = real_home
        os.chdir(real_cwd)

    print("KIRO HOOK OK")


if __name__ == "__main__":
    run()
