"""Smoke test: setup + doctor against a temp home."""

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
shutil.rmtree("/tmp/tm-test-home", ignore_errors=True)
os.environ["TRAILMEM_HOME"] = "/tmp/tm-test-home"
os.environ["TRAILMEM_DB"] = "/tmp/tm-test-home/trailmem.db"

from trailmem.cli import main  # noqa: E402


def run() -> None:
    assert main(["setup"]) == 0
    assert main(["doctor"]) == 0

    # Verify schema traps directly.
    from trailmem.schema import connect

    conn = connect()
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"memories", "edges", "sessions"} <= tables
    fts = conn.execute("SELECT name FROM sqlite_master WHERE name='memories_fts'").fetchone()
    assert fts is not None

    # --- store layer ---
    from trailmem.store import ValidationError, store

    content = "Use QTcpSocket direct connection for aria2 JSON-RPC instead of WebSocket wrapper."
    r = store(conn, content, "aria2 via QTcpSocket", "decision", agent_type="claude")
    assert r["outcome"] == "stored" and r["node_id"].startswith("mem-")

    # exact duplicate in same project → rejected
    r2 = store(conn, content, "aria2 via QTcpSocket", "decision", agent_type="claude")
    assert r2["outcome"] == "rejected_exact" and r2["duplicate"]["id"] == r["id"]

    # agent_type undetectable → hard reject (the OMEGA bug fix)
    try:
        store(conn, content + " v2 variant", "another title", "decision", env={})
        raise AssertionError("expected ValidationError for missing agent_type")
    except ValidationError:
        pass

    # constraint → auto-pinned; link_to creates edge
    r3 = store(conn, "NEVER store memory content in non-English text; embedding accuracy depends on it fully.",
               "English-only content", "constraint", agent_type="claude",
               link_to=r["node_id"], edge_type="related")
    assert r3["outcome"] == "stored" and r3["linked"]["target"] == r["node_id"]
    row = conn.execute("SELECT pinned FROM memories WHERE node_id=?", (r3["node_id"],)).fetchone()
    assert row["pinned"] == 1

    # FTS synced on store
    hit = conn.execute("SELECT node_id FROM memories_fts WHERE memories_fts MATCH 'aria2'").fetchone()
    assert hit["node_id"] == r["node_id"]

    # title too short → validation error
    try:
        store(conn, content + " v3", "ab", "decision", agent_type="claude")
        raise AssertionError("expected ValidationError for short title")
    except ValidationError:
        pass

    conn.close()
    print("SMOKE OK")


if __name__ == "__main__":
    run()
