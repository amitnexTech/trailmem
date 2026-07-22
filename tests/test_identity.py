"""Canonical SessionContext and adapter ownership invariants."""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trailmem import embeddings, mcp_server, schema, store as store_mod  # noqa: E402
from trailmem.errors import ValidationError  # noqa: E402
from trailmem.hosts import HOSTS, HOST_BY_AGENT, codex, kiro, resolve_context  # noqa: E402
from trailmem.identity import MAX_SESSION_ID_LENGTH, resolve_agent  # noqa: E402


def run() -> None:
    assert {"codex", "kiro", "claude"} <= set(HOST_BY_AGENT)

    # Every auto-discovered adapter must obey the same native-to-canonical
    # precedence contract; adding a standard host requires no shared allowlist.
    for host in HOSTS:
        payload = {
            host.session_payload[0]: "payload-id",
            host.project_payload[0]: f"/tmp/{host.agent}",
        }
        context = host.resolve_context(payload, env={})
        assert context.agent_type == host.agent
        assert context.key == f"{host.agent}:payload-id"
        assert context.project == f"/tmp/{host.agent}"
        assert context.source == f"{host.agent}-adapter"

        explicit = host.resolve_context(
            payload, env={}, session_id="explicit-id", project="/tmp/explicit")
        assert explicit.key == f"{host.agent}:explicit-id"
        assert explicit.project == "/tmp/explicit"

        overridden = host.resolve_context(
            payload,
            env={"TRAILMEM_PROJECT": "/tmp/env-project"},
        )
        assert overridden.project == "/tmp/env-project"

        if host.session_env:
            native_env = host.resolve_context(
                {}, env={host.session_env[0]: "native-env-id"})
            assert native_env.key == f"{host.agent}:native-env-id"
            portable_env = host.resolve_context(
                {},
                env={
                    "TRAILMEM_SESSION_ID": "portable-id",
                    host.session_env[0]: "native-env-id",
                },
            )
            assert portable_env.key == f"{host.agent}:portable-id"

    hook = codex.HOST.resolve_context(
        {"session_id": "thread-1", "cwd": "/tmp/project"},
        env={},
        event="tool-context",
    )
    assert hook.key == "codex:thread-1"
    assert hook.project == "/tmp/project"

    mcp = resolve_context(
        canonical=hook.to_payload(),
        env={"TRAILMEM_AGENT_TYPE": "codex"},
    )
    assert mcp == hook
    assert mcp.key == hook.key, "hook and MCP must consume the same identity"

    leaked_env = resolve_context(
        agent_type="codex",
        payload={"session_id": "thread-2", "cwd": "/tmp/project"},
        env={"TRAILMEM_AGENT_TYPE": "claude"},
    )
    assert leaked_env.agent_type == "codex", "trusted hook adapter must win"

    kctx = kiro.HOST.resolve_context(
        {"conversationId": "kiro-1", "cwd": "/tmp/k"},
        env={},
        event="session-start",
    )
    assert kctx.key == "kiro:kiro-1"

    generic = resolve_context(
        agent_type="future-agent",
        env={
            "TRAILMEM_AGENT_TYPE": "future-agent",
            "TRAILMEM_SESSION_ID": "future-1",
            "TRAILMEM_PROJECT": "/tmp/future",
        },
    )
    assert generic.key == "future-agent:future-1"
    assert generic.source == "generic-adapter"

    try:
        resolve_agent(None, {"KIRO_SESSION_ID": "must-not-leak-into-core"})
        raise AssertionError("core must not interpret host-native env vars")
    except ValidationError:
        pass

    invalid_contexts = [
        ({**hook.to_payload(), "session_id": 123}, "session_id"),
        ({
            **hook.to_payload(),
            "session_id": "x" * (MAX_SESSION_ID_LENGTH + 1),
        }, "at most"),
        ({**hook.to_payload(), "event": "response-finished"}, "event"),
        ({**hook.to_payload(), "source": ["codex-adapter"]}, "source"),
        ({**hook.to_payload(), "source": "Codex Adapter"}, "source"),
    ]
    for payload, expected in invalid_contexts:
        try:
            resolve_context(
                canonical=payload,
                env={"TRAILMEM_AGENT_TYPE": "codex"},
            )
            raise AssertionError(f"invalid canonical {expected} must be rejected")
        except ValidationError as exc:
            assert expected in str(exc), exc

    try:
        resolve_context(
            canonical={**hook.to_payload(), "event": "response-finished"},
            env={"TRAILMEM_AGENT_TYPE": "codex"},
            required=False,
        )
        raise AssertionError(
            "malformed canonical context must not silently degrade to stateless")
    except ValidationError:
        pass

    legacy = resolve_context(
        canonical={
            "schema_version": 1,
            "agent_type": "codex",
            "session_id": "legacy-envelope",
            "project": "/tmp/project",
        },
        env={"TRAILMEM_AGENT_TYPE": "codex"},
    )
    assert legacy.source == "canonical"

    try:
        resolve_context(
            canonical=hook.to_payload(),
            env={"TRAILMEM_AGENT_TYPE": "claude"},
        )
        raise AssertionError("canonical context must match the pinned MCP host")
    except ValidationError:
        pass

    old_agent = os.environ.get("TRAILMEM_AGENT_TYPE")
    os.environ["TRAILMEM_AGENT_TYPE"] = "codex"
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    old_conn = mcp_server._conn
    old_schema_config = schema.load_config
    old_store_config = store_mod.load_config
    old_embed = embeddings.embed
    try:
        disabled = lambda: {"embedding": {"enabled": False, "dimensions": 384}}
        schema.load_config = disabled
        store_mod.load_config = disabled
        embeddings.embed = lambda text: None
        schema.init_db(conn)
        mcp_server._conn = conn

        result = mcp_server.trailmem_store(
            title="Canonical identity",
            content="Canonical hook context must remain authoritative across MCP store and edit operations.",
            event_type="decision",
            code_files="trailmem/identity.py",
            doc_files="none",
            project="/tmp/legacy-conflict",
            session_id="legacy-conflict",
            session_context=hook.to_payload(),
        )
        assert "Stored #" in result, result
        node_id = result.split("[", 1)[1].split("]", 1)[0]
        mcp_server.trailmem_edit(
            ref=node_id,
            title="Canonical identity v2",
            session_id="another-conflict",
            session_context=hook.to_payload(),
        )

        rows = conn.execute(
            "SELECT session_id, write_count FROM sessions ORDER BY session_id"
        ).fetchall()
        assert [(row["session_id"], row["write_count"]) for row in rows] == [
            ("codex:thread-1", 2)
        ], rows
        memory_session = conn.execute(
            "SELECT session_id, project FROM memories WHERE node_id = ?", (node_id,)
        ).fetchone()
        assert memory_session["session_id"] == "codex:thread-1"
        assert memory_session["project"] == "/tmp/project"
    finally:
        mcp_server._conn = old_conn
        schema.load_config = old_schema_config
        store_mod.load_config = old_store_config
        embeddings.embed = old_embed
        conn.close()
        if old_agent is None:
            os.environ.pop("TRAILMEM_AGENT_TYPE", None)
        else:
            os.environ["TRAILMEM_AGENT_TYPE"] = old_agent

    # Symlink aliases of one repo must resolve to one project scope.
    import tempfile

    from trailmem.identity import resolve_project

    with tempfile.TemporaryDirectory() as td:
        real = os.path.join(td, "real")
        os.mkdir(real)
        alias = os.path.join(td, "alias")
        os.symlink(real, alias)
        assert resolve_project(alias, env={}) == resolve_project(real, env={})

    print("IDENTITY OK")


if __name__ == "__main__":
    run()
