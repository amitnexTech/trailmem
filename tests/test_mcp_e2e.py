"""E2E: spawn the MCP server over stdio (python -m — the canonical launch
shape since the trailmem-mcp script was removed) and exercise all 6 tools."""

import asyncio
import json
import os
import sqlite3
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PYTHON = sys.executable
SERVER_ARGS = ["-u", "-m", "trailmem.mcp_server"]
ENV = {
    **os.environ,
    "TRAILMEM_HOME": "/tmp/tm-mcp-e2e",
    "TRAILMEM_DB": "/tmp/tm-mcp-e2e/trailmem.db",
    "TRAILMEM_AGENT_TYPE": "claude",
    "CLAUDE_CODE_SESSION_ID": "e2e-session-1",
}


async def call(sess, tool, **kwargs):
    r = await sess.call_tool(tool, kwargs)
    text = "".join(c.text for c in r.content if c.type == "text")
    return r.isError, text


async def run():
    import shutil
    shutil.rmtree("/tmp/tm-mcp-e2e", ignore_errors=True)
    os.makedirs("/tmp/tm-mcp-e2e", exist_ok=True)
    with open("/tmp/tm-mcp-e2e/config.json", "w") as f:
        json.dump({"embedding": {"enabled": False}}, f)

    async with stdio_client(
        StdioServerParameters(command=PYTHON, args=SERVER_ARGS, env=ENV)
    ) as (read, write):
        async with ClientSession(read, write) as sess:
            await sess.initialize()
            tools = {t.name for t in (await sess.list_tools()).tools}
            assert tools == {"trailmem_welcome", "trailmem_store", "trailmem_query",
                             "trailmem_show", "trailmem_edit", "trailmem_link"}, tools

            err, w = await call(
                sess, "trailmem_welcome", session_id="explicit-session")
            assert not err and "📊" in w, w

            # code_files/doc_files omitted → error (required since 0.1.8)
            err, s0 = await call(sess, "trailmem_store",
                                 title="E2E missing files", event_type="decision",
                                 content="This store must fail: file fields are required and were not passed.")
            assert err or "code_files" in s0, s0

            err, s1 = await call(sess, "trailmem_store",
                                 title="E2E decision", event_type="decision",
                                 code_files="none", doc_files="none",
                                 session_id="explicit-session",
                                 content="Use stdio transport only for v1; HTTP deferred until a team use case exists.")
            assert not err and "Stored #" in s1, s1
            node = s1.split("[")[1].split("]")[0]

            err, s2 = await call(sess, "trailmem_store",
                                 title="E2E constraint", event_type="constraint", pinned=False,
                                 content="Never inject memory content per-turn; welcome once per session is the only injection point.",
                                 code_files="none", doc_files="none",
                                 session_id="explicit-session",
                                 link_to=node)
            assert not err and "Stored #" in s2 and "Linked" in s2, s2

            # exact dup → business outcome text, NOT protocol error
            err, s3 = await call(sess, "trailmem_store",
                                 title="E2E decision", event_type="decision",
                                 code_files="none", doc_files="none",
                                 content="Use stdio transport only for v1; HTTP deferred until a team use case exists.")
            assert not err and "Rejected: exact duplicate" in s3, s3

            err, q = await call(sess, "trailmem_query", text="stdio transport")
            assert not err and "E2E decision" in q, q

            err, sh = await call(sess, "trailmem_show", ref=node)
            assert not err and "Edges (1)" in sh and "[e" in sh, sh

            err, ed = await call(sess, "trailmem_edit", ref=node, title="E2E decision v2")
            assert not err and "title" in ed, ed
            err, ed2 = await call(
                sess, "trailmem_edit", ref=node, title="E2E decision v3",
                session_id="explicit-session")
            assert not err and "title" in ed2, ed2

            # unknown ref → protocol error (isError=True)
            err, _ = await call(sess, "trailmem_show", ref="mem-nonexist")
            assert err, "unknown ref must be a protocol error"

            err, w2 = await call(
                sess, "trailmem_welcome", session_id="explicit-session")
            assert not err and "SINCE" not in w2, "second welcome must be short (anti-bloat)"

    conn = sqlite3.connect(ENV["TRAILMEM_DB"])
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT session_id, write_count FROM sessions ORDER BY session_id"
    ).fetchall()
    by_id = {row["session_id"]: row["write_count"] for row in rows}
    assert by_id["claude:explicit-session"] == 3
    assert not any(
        sid.startswith("pid-") or ":pid-" in sid for sid in by_id
    ), by_id
    conn.close()

    print("MCP E2E OK")


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
