"""trailmem MCP server — 6 tools over stdio.

Business outcomes (dup reject, similarity block) are plain-text SUCCESS
responses with a next-action hint — never protocol errors (some clients
auto-retry protocol errors, and a dup-reject retry loops forever).
Invalid params / unknown refs ARE protocol errors (ValidationError raised).
"""

import functools
import os
import sqlite3

from mcp.server.fastmcp import FastMCP

from . import console, hosts, ops, queries, sessions, store as store_mod
from .schema import connect, init_db
from .store import ValidationError

# Self-guarding: hook-equipped hosts inject the briefing before the agent runs,
# so the instruction must not trigger a second (duplicate) welcome there.
_INSTRUCTIONS = (
    "trailmem is persistent cross-session memory. At session start, IF a trailmem "
    "briefing (pinned rules / recent activity) is not already in your context, call "
    "trailmem_welcome once — never twice. Before the session ends, store its durable "
    "decisions/lessons/tasks with trailmem_store (English content). Parameter rules: "
    "omit `project` to auto-scope to the current working directory, pass 'global' for "
    "cross-project memories; omit `agent_type` (auto-detected); `code_files` and "
    "`doc_files` are required — list the files this memory touches, or pass 'none'. "
    "If a call fails or usage is unclear, consult the 'trailmem' skill if installed."
)

mcp = FastMCP("trailmem", instructions=_INSTRUCTIONS)
_conn = None


def _db():
    global _conn
    if _conn is None:
        _conn = connect()
        init_db(_conn)
    return _conn


def _tx_safe(fn):
    """Roll back the shared connection on any tool failure. A failed write
    otherwise leaves the implicit transaction open on this long-lived
    connection, write-locking the DB for EVERY process (observed live:
    schema-mismatch INSERT from a stale server locked prod for all agents)."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            if _conn is not None:
                try:
                    _conn.rollback()
                except sqlite3.Error:
                    pass
            raise
    return wrapper


def _session_ctx(
    agent_type=None,
    project=None,
    session_id=None,
    session_context=None,
    required=True,
):
    """Resolve one canonical context and register exactly that session."""
    pinned_agent = os.environ.get("TRAILMEM_AGENT_TYPE")
    if pinned_agent and agent_type and agent_type != pinned_agent:
        raise ValidationError(
            f"agent_type {agent_type!r} conflicts with MCP host "
            f"{pinned_agent!r}")
    context = hosts.resolve_context(
        agent_type=pinned_agent or agent_type,
        canonical=session_context,
        session_id=session_id,
        project=project if session_context is None else None,
        required=required,
    )
    proj = context.project if context else store_mod.resolve_project(project)
    if context and context.key:
        sessions.register_session(
            _db(), context.key, context.agent_type, context.project)
    return context, proj


@mcp.tool()
@_tx_safe
def trailmem_welcome(
    project: str = None,
    agent_type: str = None,
    force: bool = False,
    session_id: str = None,
    session_context: dict = None,
) -> str:
    """Session-start briefing: pinned rules, recent activity, open tasks, stats.
    Call once at session start; repeat calls return the short form unless force=true."""
    context, proj = _session_ctx(
        agent_type, project, session_id, session_context)
    if not context.key:
        return sessions.stateless_welcome(
            _db(), context.agent_type, proj)
    return sessions.welcome(
        _db(), context.key, context.agent_type, proj, force=force)


@mcp.tool()
@_tx_safe
def trailmem_store(
    title: str,
    content: str,
    event_type: str,
    code_files: str,
    doc_files: str,
    agent_type: str = None,
    project: str = None,
    work_type: str = None,
    source_uri: str = None,
    pinned: bool = False,
    link_to: str = None,
    edge_type: str = "related",
    supersedes: str = None,
    archive_reason: str = None,
    force: bool = False,
    session_id: str = None,
    session_context: dict = None,
) -> str:
    """Save a new memory (decision/lesson/task/constraint/...) with optional linking.
    Store the ENGLISH version of the content; title 3-60 chars, content 50+ chars.
    code_files and doc_files are REQUIRED: comma-separated paths (code_files =
    source/config files this memory touches, doc_files = docs/spec pages), or
    the literal 'none' if this memory touches no files of that kind.
    Duplicates are rejected/blocked with the existing #id — edit that instead."""
    context, proj = _session_ctx(
        agent_type, project, session_id, session_context)
    conn = _db()
    # proj is None only for global scope; store() re-resolves, and a bare None
    # would fall back to cwd — pass the "global" sentinel through instead.
    r = store_mod.store(
        conn, content, title, event_type, context=context, work_type=work_type,
        project="global" if proj is None else proj,
        source_uri=source_uri, code_files=code_files, doc_files=doc_files,
        pinned=pinned, link_to=link_to, edge_type=edge_type, force=force,
    )
    if r["outcome"] == "rejected_exact":
        d = r["duplicate"]
        return (f"Rejected: exact duplicate of #{d['id']} [{d['node_id']}] '{d['title']}'. "
                f"Use trailmem_edit(ref='#{d['id']}').")
    if r["outcome"] == "blocked_near_dup":
        d = r["duplicate"]
        return (f"Blocked: {d['similarity']:.0%} similar to #{d['id']} [{d['node_id']}] "
                f"'{d['title']}'. trailmem_edit(ref='#{d['id']}') or force=true.")
    if r["outcome"] == "blocked_singleton":
        return r["message"]

    parts = [f"Stored #{r['id']} [{r['node_id']}] '{title}'."]
    if supersedes:
        s = ops.supersede(conn, r["node_id"], supersedes, archive_reason)
        parts.append(f"Superseded #{s['old_id']} '{s['old_title']}' (archived).")
    if r.get("linked"):
        parts.append(f"Linked to {r['linked']['target']} [{r['linked']['edge_type']}].")
    for w in r.get("warnings", []):
        parts.append(f"⚠ {w}")
    if r.get("related_candidates"):
        cands = ", ".join(f"#{c['id']} [{c['node_id']}] ({c['similarity']})"
                          for c in r["related_candidates"])
        parts.append(f"Related candidates: {cands} → trailmem_link if connected, or ignore.")
    return " ".join(parts)


@mcp.tool()
@_tx_safe
def trailmem_query(
    text: str,
    type_filter: str = None,
    agent_filter: str = None,
    project: str = None,
    limit: int = 5,
    include_archived: bool = True,
    session_id: str = None,
    session_context: dict = None,
) -> str:
    """Search memories (semantic + keyword). Returns #id, type, status, edge count [↔N]
    and a 200-char preview per hit. Use trailmem_show(ref) for full content + edges."""
    _, proj = _session_ctx(
        project=project, session_id=session_id,
        session_context=session_context, required=False)
    results = queries.query(
        _db(), text, type_filter=type_filter, agent_filter=agent_filter,
        project=proj, limit=limit, include_archived=include_archived,
    )
    return queries.format_query_results(results, text)


@mcp.tool()
@_tx_safe
def trailmem_show(
    ref: str,
    session_id: str = None,
    session_context: dict = None,
) -> str:
    """Fetch one memory in full: content, all edges (with [eN] ids), supersede chain.
    The only tool that returns edges — edge ids here are what link remove needs."""
    _session_ctx(
        session_id=session_id, session_context=session_context, required=False)
    data = queries.show(_db(), ref)
    if not data:
        raise ValidationError(f"ref '{ref}' not found")
    return queries.format_show(data)


@mcp.tool()
@_tx_safe
def trailmem_edit(
    ref: str,
    content: str = None,
    title: str = None,
    event_type: str = None,
    pinned: bool = None,
    status: str = None,
    archive_reason: str = None,
    link_to: str = None,
    edge_type: str = "related",
    session_id: str = None,
    session_context: dict = None,
) -> str:
    """Update a memory's content/title/type/pin, or close it (status='completed'/
    'cancelled' for finished/dropped tasks, 'archived' for wrong/outdated info,
    'superseded' when replaced — all need archive_reason >=20 chars AND >=1 edge).
    Content edits refresh hash + embedding + search index automatically."""
    context, _ = _session_ctx(
        session_id=session_id, session_context=session_context, required=False)
    r = ops.edit(
        _db(), ref, content=content, title=title, event_type=event_type,
        pinned=pinned, status=status, archive_reason=archive_reason,
        link_to=link_to, edge_type=edge_type,
        session_id=context.key if context else None,
    )
    what = ", ".join(r["changed"]) if r["changed"] else "nothing"
    msg = f"Updated #{r['id']} [{r['node_id']}] '{r['title']}': {what}."
    if r["linked"]:
        msg += f" Linked to {', '.join(r['linked'])}."
    return msg


@mcp.tool()
@_tx_safe
def trailmem_link(
    action: str,
    source: str = None,
    target: str = None,
    edge_type: str = None,
    metadata: str = "",
    edge_id: int = None,
    session_id: str = None,
    session_context: dict = None,
) -> str:
    """Create (action='add': source, target, edge_type) or remove (action='remove':
    edge_id from trailmem_show) a typed edge between memories.
    Types: related / derived_from / supersedes / contradicts / evolves."""
    _session_ctx(
        session_id=session_id, session_context=session_context, required=False)
    conn = _db()
    if action == "add":
        if not (source and target and edge_type):
            raise ValidationError("add requires source, target, edge_type")
        r = ops.link_add(conn, source, target, edge_type, metadata)
        if r["duplicate"]:
            return (f"Edge already exists: #{r['source_id']} → #{r['target_id']} "
                    f"[{r['edge_type']}]. No action.")
        note = f" '{metadata}'" if metadata else ""
        return f"Linked #{r['source_id']} → #{r['target_id']} [{r['edge_type']}]{note}."
    if action == "remove":
        if edge_id is None:
            raise ValidationError("remove requires edge_id (see trailmem_show)")
        r = ops.link_remove(conn, edge_id)
        msg = f"Unlinked edge #{r['edge_id']} ({r['source']} → {r['target']} [{r['edge_type']}])."
        for n in r["orphaned"]:
            mm = queries.resolve_ref(conn, n)
            msg += f" ⚠ #{mm['id']} now has 0 edges (orphan). Consider linking."
        return msg
    raise ValidationError("action must be 'add' or 'remove'")


@mcp.prompt(title="Save this session to memory")
def save_session() -> str:
    """Capture this session's durable decisions, lessons, and open tasks into
    trailmem before you exit. Portable across every MCP client that surfaces
    prompts (Claude Code, Cursor, VS Code, Windsurf, ...)."""
    return (
        "Capture the durable memory from THIS session into trailmem now, while "
        "the full conversation is still in context.\n\n"
        "1. Review the conversation and identify what is worth persisting across "
        "sessions:\n"
        "   - decision — a rule, tool choice, structure, or enforced behavior we settled on\n"
        "   - lesson — a bug, mistake, or non-obvious thing learned (include the why)\n"
        "   - task — concrete follow-up work still open\n"
        "   - constraint / user_preference — a durable rule or personal preference\n"
        "   Skip ephemeral chatter, things already stored, and anything derivable "
        "from code or git history.\n"
        "2. For each item, call the trailmem_store tool with content in ENGLISH "
        "(hard rule even if we spoke another language), the correct event_type, and "
        "a link to a related existing memory (link_to + edge_type) so it is not an "
        "orphan — query first if unsure what to link to.\n"
        "3. If trailmem_store reports a near-duplicate, update the existing memory "
        "with trailmem_edit instead of forcing a second copy.\n"
        "4. If nothing this session is genuinely worth persisting, say so plainly — "
        "do NOT invent filler memories.\n\n"
        "After saving, give a one-line summary of what you stored (ids + titles)."
    )


def main() -> None:
    # UTF-8 crash guard: MCP JSON-RPC is UTF-8; a cp1252 stdout on Windows can
    # otherwise raise mid-message. stderr carries our degrade warnings.
    console.configure()
    mcp.run()


if __name__ == "__main__":
    main()
