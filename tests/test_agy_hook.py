"""Antigravity welcome-hook invariants (facts from the 2026-07-23 self-report
+ the official agy hooks.md contract).

1. PreInvocation fires before EVERY model call → the hook must inject the
   briefing ONLY on a conversation's first fire (marker dedup) and emit a
   bare {} on every later fire. stdout must always be one JSON object.
2. Welcome is STATELESS: the conversationId never reaches the MCP server, so
   the hook must never register a session row (write_count would stick at 0
   and false-alarm every next session).
3. hooks.json holds NAMED GROUPS — install/remove own exactly the "trailmem"
   key; foreign groups (e.g. a user's hand-written one) survive untouched.
4. PreToolUse tool-context: agy dispatches MCP calls via call_mcp_tool
   {ServerName, ToolName, Arguments}; `overwrite` is a SHALLOW merge, so the
   hook echoes the FULL Arguments back with session_context added — trailmem
   calls only; foreign servers get a bare {} (never a decision).
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HOME = os.path.join(tempfile.gettempdir(), "tm-agy-hook-home")
shutil.rmtree(HOME, ignore_errors=True)
os.environ["TRAILMEM_HOME"] = f"{HOME}/.trailmem"
os.environ["TRAILMEM_DB"] = f"{HOME}/.trailmem/trailmem.db"

from trailmem.cli import main  # noqa: E402
from trailmem.hosts import _util, antigravity  # noqa: E402
from trailmem.schema import connect, init_db  # noqa: E402

PAYLOAD = {"conversationId": "5e691914-85d6-4f5f-b96f-f73410dce2bd",
           "workspacePaths": ["/tmp/agy-project"],
           "invocationNum": 1, "modelName": "auto"}


def _fire(payload, event="pre-invocation") -> dict:
    real_stdin, out = sys.stdin, io.StringIO()
    sys.stdin = io.StringIO(payload if isinstance(payload, str)
                            else json.dumps(payload))
    try:
        with contextlib.redirect_stdout(out):
            assert main(["hook", event, "--agent", "antigravity"]) == 0
    finally:
        sys.stdin = real_stdin
    return json.loads(out.getvalue())


def run() -> None:
    # --- 0. native payload mapping: camelCase conversationId is the key ---
    ctx = antigravity.HOST.resolve_context(PAYLOAD)
    assert ctx.session_id == PAYLOAD["conversationId"]
    assert ctx.session_id and ctx.key == f"antigravity:{ctx.session_id}"

    # --- 1. first fire injects, second fire is {} ---
    first = _fire(PAYLOAD)
    steps = first.get("injectSteps")
    assert steps and "userMessage" in steps[0], first
    assert steps[0]["userMessage"].startswith("[trailmem session briefing"), \
        "context-only preamble expected"
    assert "trailmem" in steps[0]["userMessage"].lower() \
        or "📊" in steps[0]["userMessage"], "briefing text expected"
    marker_dir = Path(HOME) / ".trailmem" / "welcomed"
    assert any(marker_dir.iterdir()), "dedup marker must be written"

    assert _fire(PAYLOAD) == {}, "second fire must inject nothing"

    # session-aware since the transport was live-proven: first fire registers
    # the session row under antigravity:<conversationId>
    conn = connect()
    init_db(conn)
    rows = conn.execute(
        "SELECT session_id, write_count FROM sessions "
        "WHERE agent_type = 'antigravity'").fetchall()
    assert [(r["session_id"], r["write_count"]) for r in rows] == \
        [(f"antigravity:{PAYLOAD['conversationId']}", 0)], \
        "first fire must register the session row at write_count 0"
    conn.close()

    # no id / garbage stdin → bare {} and exit 0, never a crash
    assert _fire({"modelName": "auto"}) == {}
    assert _fire("not json at all") == {}

    # --- 1b. PreToolUse tool-context: rewrite trailmem calls only ---
    tool_payload = dict(PAYLOAD)
    tool_payload["stepIdx"] = 4
    tool_payload["toolCall"] = {"name": "call_mcp_tool", "args": {
        "ServerName": "trailmem", "ToolName": "trailmem_query",
        "Arguments": {"text": "session identity", "limit": 3},
        "toolAction": "Searching memory", "toolSummary": "query"}}
    out = _fire(tool_payload, event="tool-context")
    assert out["decision"] == "allow", out
    assert set(out["overwrite"]) == {"Arguments"}, \
        "only Arguments may be overwritten — ServerName/ToolName untouched"
    inner = out["overwrite"]["Arguments"]
    assert inner["text"] == "session identity" and inner["limit"] == 3, \
        "original params must survive the shallow-merge echo"
    assert inner["session_context"] == {
        "schema_version": 1,
        "agent_type": "antigravity",
        "session_id": PAYLOAD["conversationId"],
        "project": PAYLOAD["workspacePaths"][0],
        "event": "tool-context",
        "source": "antigravity-adapter",
    }

    # zero-param tool (e.g. trailmem_welcome) → Arguments is just the context
    no_args = dict(tool_payload)
    no_args["toolCall"] = {"name": "call_mcp_tool", "args": {
        "ServerName": "trailmem", "ToolName": "trailmem_welcome"}}
    out = _fire(no_args, event="tool-context")
    assert set(out["overwrite"]["Arguments"]) == {"session_context"}

    # foreign MCP server → bare {} no-op, NEVER a decision for tools not ours
    foreign_call = dict(tool_payload)
    foreign_call["toolCall"] = {"name": "call_mcp_tool", "args": {
        "ServerName": "github", "ToolName": "create_issue",
        "Arguments": {"title": "x"}}}
    assert _fire(foreign_call, event="tool-context") == {}

    # malformed toolCall / garbage stdin → {} and exit 0
    assert _fire({"toolCall": {"name": "call_mcp_tool"}, **PAYLOAD},
                 event="tool-context") == {}
    assert _fire("not json at all", event="tool-context") == {}

    # --- 2. hooks.json lifecycle: named-group surgery ---
    real_home = _util._HOME
    _util._HOME = lambda: Path(HOME)
    try:
        path = Path(HOME) / ".gemini" / "config" / "hooks.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        foreign = {"my-linter": {"PostToolUse": [{"matcher": "*", "hooks": [
            {"command": "./lint.sh"}]}]}}
        path.write_text(json.dumps(foreign))

        msg = antigravity.install_hook()
        assert "written" in msg, msg
        data = json.loads(path.read_text())
        assert "my-linter" in data, "foreign hook group must survive install"
        handler = data["trailmem"]["PreInvocation"][0]
        assert "-m trailmem hook pre-invocation --agent antigravity" in handler["command"]
        assert handler["timeout"] > 0
        assert set(data["trailmem"]) == {"PreInvocation", "PreToolUse"}, \
            "only the deduped welcome + the silent tool-context rewrite"
        tool_group = data["trailmem"]["PreToolUse"][0]
        assert tool_group["matcher"] == "call_mcp_tool"
        assert "-m trailmem hook tool-context --agent antigravity" \
            in tool_group["hooks"][0]["command"]
        assert antigravity._hook_check() == "installed"

        assert "already installed" in antigravity.install_hook()

        # pre-0.1.9 group (welcome only) → flagged by doctor, upgraded in place
        old = {"PreInvocation": data["trailmem"]["PreInvocation"]}
        data["trailmem"] = old
        path.write_text(json.dumps(data))
        assert "welcome only" in antigravity._hook_check()
        assert "updated" in antigravity.install_hook()
        assert antigravity._hook_check() == "installed"

        data = json.loads(path.read_text())
        data["trailmem"]["PreInvocation"][0]["command"] = "stale"
        path.write_text(json.dumps(data))
        assert "updated" in antigravity.install_hook()

        msg = antigravity.remove_hook()
        assert msg is not None
        data = json.loads(path.read_text())
        assert "trailmem" not in data and "my-linter" in data
        assert antigravity.remove_hook() is None, "second removal is a no-op"
        assert antigravity._hook_check() == "not installed"

        # JSONC → refuse to rewrite, manual instruction
        path.write_text('{/* comment */ "trailmem": {}}')
        for fn in (antigravity.install_hook, antigravity.remove_hook):
            try:
                fn()
                raise AssertionError("JSONC must raise, not rewrite")
            except RuntimeError as exc:
                assert "manually" in str(exc)
        assert "/* comment */" in path.read_text()
    finally:
        _util._HOME = real_home

    print("AGY HOOK OK")


if __name__ == "__main__":
    run()
