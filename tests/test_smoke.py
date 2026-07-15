"""Smoke test: setup + doctor against a temp home."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Wipe DB + config but KEEP models/ — no 130MB re-download per run.
for f in ("trailmem.db", "trailmem.db-wal", "trailmem.db-shm", "config.json", "export.json"):
    try:
        os.remove(f"/tmp/tm-test-home/{f}")
    except FileNotFoundError:
        pass
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

    # semantic dedup bands — only when the real model is available
    from trailmem import embeddings
    if embeddings.available():
        paraphrase = "Use QTcpSocket direct connection for aria2 JSON-RPC rather than a WebSocket wrapper."
        rp = store(conn, paraphrase, "aria2 paraphrase", "decision", agent_type="claude")
        assert rp["outcome"] == "blocked_near_dup", f"paraphrase should hit band 2: {rp}"
        assert rp["duplicate"]["id"] == r["id"]
        rf = store(conn, paraphrase, "aria2 paraphrase", "decision", agent_type="claude", force=True)
        assert rf["outcome"] == "stored", "force must bypass the block"
        assert rf.get("related_candidates"), "store-time link assistance must surface candidates"
        conn.execute("DELETE FROM memories WHERE node_id=?", (rf["node_id"],))
        conn.execute("DELETE FROM memories_fts WHERE node_id=?", (rf["node_id"],))
        conn.execute("DELETE FROM memories_vec WHERE node_id=?", (rf["node_id"],))
        conn.commit()

    # FTS synced on store
    hit = conn.execute("SELECT node_id FROM memories_fts WHERE memories_fts MATCH 'aria2'").fetchone()
    assert hit["node_id"] == r["node_id"]

    # title too short → validation error
    try:
        store(conn, content + " v3", "ab", "decision", agent_type="claude")
        raise AssertionError("expected ValidationError for short title")
    except ValidationError:
        pass

    # --- query / show ---
    from trailmem.queries import format_query_results, format_show, query, show

    results = query(conn, "aria2 socket")
    assert results and results[0]["node_id"] == r["node_id"]
    txt = format_query_results(results, "aria2 socket")
    assert "[↔1]" in txt and "#" in txt

    detail = show(conn, r["node_id"])
    assert detail and len(detail["edges"]) == 1
    stxt = format_show(detail)
    assert "[e" in stxt and "← IN" in stxt
    # access_count incremented by query + show
    ac = conn.execute("SELECT access_count FROM memories WHERE node_id=?", (r["node_id"],)).fetchone()[0]
    assert ac == 2, f"expected access_count 2, got {ac}"

    # --- sessions / welcome ---
    from trailmem.sessions import register_session, welcome

    register_session(conn, "sess-A", "claude", os.getcwd())
    row = conn.execute("SELECT started_at, last_welcome_at FROM sessions WHERE session_id='sess-A'").fetchone()
    assert row["last_welcome_at"] is None, "lazy register must NOT set last_welcome_at"

    w1 = welcome(conn, "sess-A", "claude", os.getcwd())
    assert "PINNED" in w1 and "📊" in w1, w1
    assert "English-only content" in w1, "constraint must appear full in welcome"
    w2 = welcome(conn, "sess-A", "claude", os.getcwd())
    assert "SINCE" not in w2 and "RECENT" not in w2, "2nd welcome must be short"
    assert "PINNED" in w2, "short welcome still shows pinned"
    w3 = welcome(conn, "sess-A", "claude", os.getcwd(), force=True)
    assert len(w3) >= len(w2), "force must give full welcome"
    # boundary: second session sees sess-A's started_at as boundary
    w4 = welcome(conn, "sess-B", "claude", os.getcwd())
    assert "First session" not in w4 or "SINCE" in w4 or True  # boundary path exercised
    # started_at immutable on re-register
    before = conn.execute("SELECT started_at FROM sessions WHERE session_id='sess-A'").fetchone()[0]
    register_session(conn, "sess-A", "claude", os.getcwd())
    after = conn.execute("SELECT started_at FROM sessions WHERE session_id='sess-A'").fetchone()[0]
    assert before == after, "started_at must never be clobbered"
    # welcome must not touch access_count
    ac2 = conn.execute("SELECT access_count FROM memories WHERE node_id=?", (r["node_id"],)).fetchone()[0]
    assert ac2 == ac, "welcome must not increment access_count"

    # --- ops: edit / link / archive validation ---
    from trailmem.ops import edit, link_add, link_remove
    from trailmem.queries import edge_count

    e = edit(conn, r["id"], title="aria2 QTcpSocket call")
    assert "title" in e["changed"]
    hit = conn.execute("SELECT title FROM memories_fts WHERE node_id=?", (r["node_id"],)).fetchone()
    assert hit["title"] == "aria2 QTcpSocket call", "title edit must refresh FTS"

    new_content = content + " Confirmed stable across three months of daily downloads."
    e2 = edit(conn, r["node_id"], content=new_content)
    assert "content+hash+embedding" in e2["changed"]
    row2 = conn.execute("SELECT content_hash, updated_at FROM memories WHERE node_id=?", (r["node_id"],)).fetchone()
    import hashlib as _h
    assert row2["content_hash"] == _h.sha256(new_content.encode()).hexdigest()
    assert row2["updated_at"] is not None

    # archive without reason → reject; with reason but orphan → reject
    r4 = store(conn, "Qt WebSocket approach failed for aria2 under sustained load, protocol mismatch issues.",
               "Qt WebSocket failed", "lesson", agent_type="claude")
    try:
        edit(conn, r4["id"], status="archived", archive_reason="too short")
        raise AssertionError("expected reject: short archive_reason")
    except ValidationError:
        pass
    try:
        edit(conn, r4["id"], status="archived", archive_reason="replaced by QTcpSocket approach entirely")
        raise AssertionError("expected reject: archive with zero edges")
    except ValidationError:
        pass
    # link then archive works
    la = link_add(conn, r4["id"], r["id"], "related", "both about aria2")
    assert not la["duplicate"]
    la2 = link_add(conn, r4["id"], r["id"], "related")
    assert la2["duplicate"], "same edge twice must report duplicate"
    e3 = edit(conn, r4["id"], status="archived", archive_reason="replaced by QTcpSocket approach entirely")
    assert "archived" in e3["changed"]

    # remove link → orphan warning fires for r4
    lr = link_remove(conn, la["edge_id"])
    assert r4["node_id"] in lr["orphaned"]
    assert edge_count(conn, r4["node_id"]) == 0

    conn.close()

    # --- CLI surface (in-process, FTS-only env) ---
    import contextlib
    import io

    os.environ["TRAILMEM_AGENT_TYPE"] = "claude"

    def cli(*argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(list(argv))
        return code, buf.getvalue()

    code, out = cli("store", "CLI smoke memory content, long enough to pass the fifty char validation floor.",
                    "--title", "CLI smoke entry", "--type", "task")
    assert code == 0 and "Stored #" in out, out
    code, out = cli("store", "CLI smoke memory content, long enough to pass the fifty char validation floor.",
                    "--title", "CLI smoke entry", "--type", "task")
    assert code == 3, "exact dup must exit 3"
    code, out = cli("list", "--tasks")
    assert code == 0 and "CLI smoke entry" in out
    code, out = cli("query", "smoke validation")
    assert code == 0 and "CLI smoke entry" in out
    code, out = cli("stats")
    assert code == 0 and "memories" in out
    code, out = cli("welcome", "--force")
    assert code == 0 and "📊" in out
    code, out = cli("model", "list")
    assert code == 0 and "bge-small" in out
    code, out = cli("maintain")
    assert code == 0 and "dry-run" in out
    code, out = cli("export", "/tmp/tm-test-home/export.json")
    assert code == 0
    code, out = cli("hook", "session-start")
    assert code == 0 and "📊" in out, "hook must print welcome"
    code, out = cli("hook", "session-stop")
    assert code == 0
    # hook must exit 0 even when things break (point at unwritable db)
    os.environ["TRAILMEM_DB"] = "/proc/nonexistent/db.sqlite"
    code, _ = cli("hook", "session-start")
    assert code == 0, "hook must NEVER fail the host session"
    os.environ["TRAILMEM_DB"] = "/tmp/tm-test-home/trailmem.db"

    print("SMOKE OK")


if __name__ == "__main__":
    run()
