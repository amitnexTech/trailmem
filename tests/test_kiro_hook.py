"""Kiro hook adapter invariants.

1. `trailmem hook session-start` reads the host event JSON from stdin —
   session_id + cwd land in the sessions row. Unlike Codex, Kiro's exact
   stdin payload key for the session id was unconfirmed as of 2026-07-22
   (Kiro's own logs use "conversationId"), so this also exercises the
   native payload mapping in the Kiro adapter directly.
2. integrate writes/removes ~/.kiro/hooks/trailmem-session-start.json — a
   DEDICATED file (Kiro has no shared hooks registry like Codex's
   hooks.json), so lifecycle is create/update-in-place/delete, not merge.
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
    # --- 0. Kiro owns its native payload mapping ---
    assert kiro.HOST.resolve_context({"session_id": "a"}).session_id == "a"
    assert kiro.HOST.resolve_context(
        {"conversationId": "sess_b"}).session_id == "sess_b"
    assert kiro.HOST.resolve_context({"sessionId": "c"}).session_id == "c"
    assert kiro.HOST.resolve_context({"unrelated": "x"}).session_id is None
    # priority: session_id wins over conversationId if both somehow present
    assert kiro.HOST.resolve_context(
        {"session_id": "a", "conversationId": "b"}).session_id == "a"

    # --- 1. stdin payload → session row, via the key Kiro's own logs use ---
    payload = {"conversationId": "sess_kiro-abc", "cwd": "/tmp/some-project"}
    real_stdin = sys.stdin
    sys.stdin = io.StringIO(json.dumps(payload))
    try:
        assert main(["hook", "session-start", "--agent", "kiro"]) == 0
    finally:
        sys.stdin = real_stdin
    conn = connect()
    init_db(conn)
    row = conn.execute("SELECT * FROM sessions WHERE session_id = ?",
                       ("kiro:sess_kiro-abc",)).fetchone()
    assert row is not None, "stdin conversationId must register a real session"
    assert row["agent_type"] == "kiro" and row["project"] == "/tmp/some-project"
    conn.close()

    # No session id → stateless briefing, never a fake adhoc/pid session.
    sys.stdin = io.StringIO(json.dumps({"cwd": "/tmp/x"}))
    try:
        assert main(["hook", "session-start", "--agent", "kiro"]) == 0
    finally:
        sys.stdin = real_stdin
    conn = connect()
    init_db(conn)
    fake = conn.execute(
        "SELECT session_id FROM sessions WHERE session_id LIKE 'adhoc-%' "
        "OR session_id LIKE 'pid-%'"
    ).fetchall()
    assert not fake
    conn.close()

    # garbage stdin must never fail the host
    sys.stdin = io.StringIO("not json at all")
    try:
        assert main(["hook", "session-start", "--agent", "kiro"]) == 0
    finally:
        sys.stdin = real_stdin

    # --- 2. hook FILE lifecycle (dedicated file, not a shared registry) ---
    real_home = _util._HOME
    _util._HOME = lambda: Path(HOME)
    try:
        path = Path(HOME) / ".kiro" / "hooks" / "trailmem-session-start.json"

        msg = kiro.install_hook()
        assert "written" in msg, msg
        data = json.loads(path.read_text())
        assert data["hooks"][0]["trigger"] == "SessionStart"
        assert "trailmem hook session-start --agent kiro" in data["hooks"][0]["action"]["command"]
        assert data["hooks"][0]["action"]["timeout"] > 0
        assert not any(h.get("trigger") == "Stop" for h in data["hooks"]), \
            "no per-turn Stop hook — same hard rule as Codex"

        assert kiro.install_hook() == "SessionStart hook already installed"

        # stale/edited file → refreshed in place, not left as-is
        data["hooks"][0]["action"]["command"] = "stale command"
        path.write_text(json.dumps(data))
        assert "updated" in kiro.install_hook()

        assert kiro.remove_hook() is not None
        assert not path.exists()
        assert kiro.remove_hook() is None, "second removal is a no-op"
    finally:
        _util._HOME = real_home

    print("KIRO HOOK OK")


if __name__ == "__main__":
    run()
