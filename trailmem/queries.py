"""Read operations: resolve refs, query (vec+FTS merged rank), show (full + edges).

show is the ONLY surface that returns edges/supersede chains; query stays lean.
access_count increments on query results and show — never on welcome.
"""

import re
import sqlite3

from . import embeddings
from .store import now


def resolve_ref(conn: sqlite3.Connection, ref) -> sqlite3.Row | None:
    """Accept '#4' / '4' / 4 (id) or 'mem-abc123' (node_id)."""
    s = str(ref).lstrip("#")
    if s.isdigit():
        return conn.execute("SELECT * FROM memories WHERE id = ?", (int(s),)).fetchone()
    return conn.execute("SELECT * FROM memories WHERE node_id = ?", (s,)).fetchone()


def edge_count(conn: sqlite3.Connection, node_id: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM edges WHERE source_node_id = ? OR target_node_id = ?",
        (node_id, node_id),
    ).fetchone()[0]


def _touch(conn: sqlite3.Connection, node_ids: list[str]) -> None:
    """access_count += 1, last_accessed = now. FTS deliberately NOT re-synced."""
    ts = now()
    conn.executemany(
        "UPDATE memories SET access_count = access_count + 1, last_accessed = ? WHERE node_id = ?",
        [(ts, n) for n in node_ids],
    )
    conn.commit()


def query(
    conn: sqlite3.Connection,
    text: str,
    *,
    type_filter: str | None = None,
    agent_filter: str | None = None,
    project: str | None = None,
    limit: int = 5,
    include_archived: bool = True,
) -> list[dict]:
    """Merged rank: vector 0.7 + FTS 0.3; archived/superseded weighted 0.5x."""
    scores: dict[str, float] = {}

    vec = embeddings.embed(text)
    if vec is not None:
        for r in conn.execute(
            "SELECT node_id, (1.0 - distance) AS sim FROM memories_vec "
            "WHERE embedding MATCH ? AND k = 20 ORDER BY distance",
            (vec.tobytes(),),
        ):
            scores[r["node_id"]] = 0.7 * r["sim"]

    # OR semantics with prefix match — FTS default AND is too strict for
    # recall-style queries ("aria2 socket" should hit "QTcpSocket for aria2").
    tokens = [t for t in re.findall(r"\w+", text) if t]
    fts_rows = []
    if tokens:
        match = " OR ".join(f'"{t}"*' for t in tokens)
        fts_rows = conn.execute(
            "SELECT node_id, rank FROM memories_fts WHERE memories_fts MATCH ? "
            "ORDER BY rank LIMIT 20",
            (match,),
        ).fetchall()
    for i, r in enumerate(fts_rows):
        # bm25 rank → position-based score (best hit 1.0, decaying)
        scores[r["node_id"]] = scores.get(r["node_id"], 0.0) + 0.3 * (1.0 - i / 20)

    results = []
    for node_id, score in scores.items():
        m = conn.execute("SELECT * FROM memories WHERE node_id = ?", (node_id,)).fetchone()
        if not m:
            continue
        if m["status"] != "active":
            if not include_archived:
                continue
            score *= 0.5
        if type_filter and m["event_type"] != type_filter:
            continue
        if agent_filter and m["agent_type"] != agent_filter:
            continue
        if project and m["project"] not in (project, None):
            continue
        results.append((score, m))

    results.sort(key=lambda t: -t[0])
    top = [m for _, m in results[:limit]]
    _touch(conn, [m["node_id"] for m in top])
    return [
        {**dict(m), "edge_count": edge_count(conn, m["node_id"])}
        for m in top
    ]


def format_query_results(results: list[dict], text: str) -> str:
    if not results:
        return f'(0 results for "{text}")'
    lines = []
    for m in results:
        pin = " [pinned]" if m["pinned"] else ""
        lines.append(
            f"#{m['id']} [{m['node_id']}] [{m['event_type']}] [{m['agent_type']}] "
            f"[{m['status']}] [↔{m['edge_count']}]{pin} {m['title']}\n"
            f"  {m['content'][:200]}{'...' if len(m['content']) > 200 else ''}"
        )
    lines.append(f'({len(results)} results for "{text}")')
    return "\n\n".join(lines)


def show(conn: sqlite3.Connection, ref) -> dict | None:
    m = resolve_ref(conn, ref)
    if not m:
        return None
    node_id = m["node_id"]
    edges = conn.execute(
        "SELECT e.*, ms.id AS source_id, ms.title AS source_title, "
        "mt.id AS target_id, mt.title AS target_title "
        "FROM edges e JOIN memories ms ON ms.node_id = e.source_node_id "
        "JOIN memories mt ON mt.node_id = e.target_node_id "
        "WHERE e.source_node_id = ? OR e.target_node_id = ?",
        (node_id, node_id),
    ).fetchall()
    chain = _supersede_chain(conn, node_id)
    _touch(conn, [node_id])
    return {"memory": dict(m), "edges": [dict(e) for e in edges], "chain": chain}


def _supersede_chain(conn: sqlite3.Connection, node_id: str) -> list[dict]:
    """Walk supersedes edges both directions: newest first. Empty if standalone."""
    def newer(n):  # who supersedes n
        r = conn.execute(
            "SELECT source_node_id FROM edges WHERE target_node_id = ? AND edge_type = 'supersedes'",
            (n,),
        ).fetchone()
        return r["source_node_id"] if r else None

    def older(n):  # whom n supersedes
        r = conn.execute(
            "SELECT target_node_id FROM edges WHERE source_node_id = ? AND edge_type = 'supersedes'",
            (n,),
        ).fetchone()
        return r["target_node_id"] if r else None

    head, seen = node_id, {node_id}
    while (up := newer(head)) and up not in seen:
        head, _ = up, seen.add(up)
    chain, cur = [], head
    while cur and cur not in {c["node_id"] for c in chain}:
        m = conn.execute(
            "SELECT id, node_id, title, status FROM memories WHERE node_id = ?", (cur,)
        ).fetchone()
        if not m:
            break
        chain.append(dict(m))
        cur = older(cur)
    return chain if len(chain) > 1 else []


def format_show(data: dict) -> str:
    m = data["memory"]
    out = [
        f"#{m['id']} [{m['node_id']}] {m['title']}",
        f"  Type: {m['event_type']} | Agent: {m['agent_type']} | Status: {m['status']}",
        f"  Created: {m['created_at'][:10]}" + (f" | Updated: {m['updated_at'][:10]}" if m["updated_at"] else ""),
        f"  Access count: {m['access_count']} | Pinned: {'yes' if m['pinned'] else 'no'}",
    ]
    if m["archive_reason"]:
        out.append(f"  Archive reason: \"{m['archive_reason']}\"")
    out.append(f"\n  Content:\n  {m['content']}")

    if data["edges"]:
        out.append(f"\n  Edges ({len(data['edges'])}):")
        for e in data["edges"]:
            meta = f' "{e["metadata"]}"' if e["metadata"] else ""
            if e["source_node_id"] == m["node_id"]:
                out.append(f"  [e{e['id']}] → OUT #{e['target_id']} [{e['target_node_id']}] [{e['edge_type']}]{meta}")
            else:
                out.append(f"  [e{e['id']}] ← IN  #{e['source_id']} [{e['source_node_id']}] [{e['edge_type']}]{meta}")
    else:
        out.append("\n  Edges: none (orphan)")

    if data["chain"]:
        out.append("\n  Supersede chain:")
        out.append("  " + " → supersedes → ".join(
            f"#{c['id']} ({c['status']})" + (" (this)" if c["node_id"] == m["node_id"] else "")
            for c in data["chain"]
        ))
        out.append(f"  Current: #{data['chain'][0]['id']} \"{data['chain'][0]['title']}\"")
    return "\n".join(out)
