"""trailmem MCP server — 6 tools over stdio.

Business outcomes (dup reject, similarity block) are plain-text SUCCESS
responses with a next-action hint — never protocol errors (some clients
auto-retry protocol errors, and a dup-reject retry loops forever).
Invalid params / unknown refs ARE protocol errors (ValidationError raised).
"""

import os

from mcp.server.fastmcp import FastMCP

from . import ops, queries, sessions, store as store_mod
from .schema import connect, init_db
from .store import ValidationError

mcp = FastMCP("trailmem")
_conn = None


def _db():
    global _conn
    if _conn is None:
        _conn = connect()
        init_db(_conn)
    return _conn


def _session_ctx(agent_type=None, project=None):
    """Resolve identity + lazily register the session (never sets last_welcome_at)."""
    agent = store_mod.resolve_agent(agent_type)
    proj = store_mod.resolve_project(project)
    sid = store_mod.session_id_from_env() or f"pid-{os.getppid()}"
    sessions.register_session(_db(), sid, agent, proj)
    return sid, agent, proj


@mcp.tool()
def trailmem_welcome(project: str = None, agent_type: str = None, force: bool = False) -> str:
    """Session-start briefing: pinned rules, recent activity, open tasks, stats.
    Call once at session start; repeat calls return the short form unless force=true."""
    sid, agent, proj = _session_ctx(agent_type, project)
    return sessions.welcome(_db(), sid, agent, proj, force=force)


@mcp.tool()
def trailmem_store(
    title: str,
    content: str,
    event_type: str,
    agent_type: str = None,
    project: str = None,
    work_type: str = None,
    source_uri: str = None,
    modified_files: str = None,
    pinned: bool = False,
    link_to: str = None,
    edge_type: str = "related",
    supersedes: str = None,
    archive_reason: str = None,
    force: bool = False,
) -> str:
    """Save a new memory (decision/lesson/task/constraint/...) with optional linking.
    Store the ENGLISH version of the content; title 3-60 chars, content 50+ chars.
    Duplicates are rejected/blocked with the existing #id — edit that instead."""
    sid, agent, proj = _session_ctx(agent_type, project)
    conn = _db()
    r = store_mod.store(
        conn, content, title, event_type, agent_type=agent, work_type=work_type,
        project=proj, session_id=sid, source_uri=source_uri, modified_files=modified_files,
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
def trailmem_query(
    text: str,
    type_filter: str = None,
    agent_filter: str = None,
    project: str = None,
    limit: int = 5,
    include_archived: bool = True,
) -> str:
    """Search memories (semantic + keyword). Returns #id, type, status, edge count [↔N]
    and a 200-char preview per hit. Use trailmem_show(ref) for full content + edges."""
    _, _, proj = _session_ctx(project=project)
    results = queries.query(
        _db(), text, type_filter=type_filter, agent_filter=agent_filter,
        project=proj, limit=limit, include_archived=include_archived,
    )
    return queries.format_query_results(results, text)


@mcp.tool()
def trailmem_show(ref: str) -> str:
    """Fetch one memory in full: content, all edges (with [eN] ids), supersede chain.
    The only tool that returns edges — edge ids here are what link remove needs."""
    _session_ctx()
    data = queries.show(_db(), ref)
    if not data:
        raise ValidationError(f"ref '{ref}' not found")
    return queries.format_show(data)


@mcp.tool()
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
) -> str:
    """Update a memory's content/title/type/pin, or archive it (status='archived'
    needs archive_reason >=20 chars AND at least one edge).
    Content edits refresh hash + embedding + search index automatically."""
    _session_ctx()
    r = ops.edit(
        _db(), ref, content=content, title=title, event_type=event_type,
        pinned=pinned, status=status, archive_reason=archive_reason,
        link_to=link_to, edge_type=edge_type,
    )
    what = ", ".join(r["changed"]) if r["changed"] else "nothing"
    msg = f"Updated #{r['id']} [{r['node_id']}] '{r['title']}': {what}."
    if r["linked"]:
        msg += f" Linked to {', '.join(r['linked'])}."
    return msg


@mcp.tool()
def trailmem_link(
    action: str,
    source: str = None,
    target: str = None,
    edge_type: str = None,
    metadata: str = "",
    edge_id: int = None,
) -> str:
    """Create (action='add': source, target, edge_type) or remove (action='remove':
    edge_id from trailmem_show) a typed edge between memories.
    Types: related / derived_from / supersedes / contradicts / evolves."""
    _session_ctx()
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
    mcp.run()


if __name__ == "__main__":
    main()
