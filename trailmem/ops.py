"""Edit + link operations.

Content edits ripple all-or-nothing: hash + embedding + FTS + updated_at.
Archive/supersede is validated (reason >=20 chars, >=1 edge) before any write.
"""

import hashlib
import sqlite3

from . import embeddings
from .identity import resolve_project
from .queries import edge_count, resolve_ref
from .schema import has_vec
from .store import EDGE_TYPES, EVENT_TYPES, ValidationError, now


def edit(
    conn: sqlite3.Connection,
    ref,
    *,
    content: str | None = None,
    title: str | None = None,
    event_type: str | None = None,
    pinned: bool | None = None,
    status: str | None = None,
    archive_reason: str | None = None,
    link_to=None,
    edge_type: str = "related",
    project: str | None = None,
    session_id: str | None = None,
) -> dict:
    m = resolve_ref(conn, ref)
    if not m:
        raise ValidationError(f"ref '{ref}' not found")
    node_id = m["node_id"]
    changed = []

    if status is not None:
        if status not in ("archived", "superseded", "completed", "cancelled"):
            raise ValidationError(
                "status must be 'archived', 'superseded', 'completed' or 'cancelled'")
        if not archive_reason or len(archive_reason) < 20:
            raise ValidationError(
                f"Cannot close as {status}: archive_reason required (min 20 chars).")
        if edge_count(conn, node_id) == 0 and not link_to:
            raise ValidationError(
                f"Cannot close as {status}: no edges exist. "
                "Link to related memory first (link or link_to=)."
            )

    if title is not None:
        if not (3 <= len(title) <= 60):
            raise ValidationError(f"title must be 3-60 chars (got {len(title)})")
        conn.execute("UPDATE memories SET title = ? WHERE node_id = ?", (title, node_id))
        changed.append("title")
    if event_type is not None:
        if event_type not in EVENT_TYPES:
            raise ValidationError(f"event_type must be one of {sorted(EVENT_TYPES)}")
        conn.execute("UPDATE memories SET event_type = ? WHERE node_id = ?", (event_type, node_id))
        changed.append("type")
    if pinned is not None:
        conn.execute("UPDATE memories SET pinned = ? WHERE node_id = ?", (int(pinned), node_id))
        changed.append("pinned" if pinned else "unpinned")
    if project is not None:
        # Rescope only — content/hash/embedding untouched. resolve_project
        # validates (abs path or 'global') and canonicalizes; 'global' → NULL.
        new_project = resolve_project(project)
        conn.execute("UPDATE memories SET project = ? WHERE node_id = ?",
                     (new_project, node_id))
        changed.append(f"project→{new_project or 'global'}")

    if content is not None:
        if len(content) < 50:
            raise ValidationError(f"content must be >=50 chars (got {len(content)})")
        # The four-way ripple: row + hash, vec, fts — bound together.
        conn.execute(
            "UPDATE memories SET content = ?, content_hash = ? WHERE node_id = ?",
            (content, hashlib.sha256(content.encode()).hexdigest(), node_id),
        )
        new_title = title if title is not None else m["title"]
        conn.execute("DELETE FROM memories_fts WHERE node_id = ?", (node_id,))
        conn.execute(
            "INSERT INTO memories_fts (node_id, title, content) VALUES (?, ?, ?)",
            (node_id, new_title, content),
        )
        vec = embeddings.embed(content)
        if vec is not None and has_vec(conn):
            conn.execute("DELETE FROM memories_vec WHERE node_id = ?", (node_id,))
            conn.execute(
                "INSERT INTO memories_vec (node_id, embedding) VALUES (?, ?)",
                (node_id, vec.tobytes()),
            )
        changed.append("content+hash+embedding")
    elif title is not None:
        # Title-only change still needs the FTS row refreshed.
        conn.execute("DELETE FROM memories_fts WHERE node_id = ?", (node_id,))
        conn.execute(
            "INSERT INTO memories_fts (node_id, title, content) VALUES (?, ?, ?)",
            (node_id, title, m["content"]),
        )

    linked = []
    if link_to:
        for target_ref in ([link_to] if isinstance(link_to, (str, int)) else link_to):
            e = link_add(conn, node_id, target_ref, edge_type)
            linked.append(e["target"])

    if status is not None:
        conn.execute(
            "UPDATE memories SET status = ?, archive_reason = ? WHERE node_id = ?",
            (status, archive_reason, node_id),
        )
        changed.append(status)

    if changed or linked:
        ts = now()
        conn.execute("UPDATE memories SET updated_at = ? WHERE node_id = ?", (ts, node_id))
        if session_id:
            conn.execute(
                "UPDATE sessions SET write_count = COALESCE(write_count, 0) + 1, "
                "last_write_at = ?, last_seen_at = ? WHERE session_id = ?",
                (ts, ts, session_id),
            )
    conn.commit()
    return {"id": m["id"], "node_id": node_id, "title": title or m["title"],
            "changed": changed, "linked": linked}


def link_add(conn: sqlite3.Connection, source_ref, target_ref, edge_type: str,
             metadata: str = "") -> dict:
    if edge_type not in EDGE_TYPES:
        raise ValidationError(f"edge_type must be one of {sorted(EDGE_TYPES)}")
    src = resolve_ref(conn, source_ref)
    tgt = resolve_ref(conn, target_ref)
    if not src:
        raise ValidationError(f"source ref '{source_ref}' not found")
    if not tgt:
        raise ValidationError(f"target ref '{target_ref}' not found")
    if src["node_id"] == tgt["node_id"]:
        raise ValidationError("cannot link a memory to itself")
    existing = conn.execute(
        "SELECT id FROM edges WHERE source_node_id = ? AND target_node_id = ? AND edge_type = ?",
        (src["node_id"], tgt["node_id"], edge_type),
    ).fetchone()
    if existing:
        return {"duplicate": True, "edge_id": existing["id"],
                "source": src["node_id"], "target": tgt["node_id"], "edge_type": edge_type,
                "source_id": src["id"], "target_id": tgt["id"]}
    cur = conn.execute(
        "INSERT INTO edges (source_node_id, target_node_id, edge_type, metadata, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (src["node_id"], tgt["node_id"], edge_type, metadata, now()),
    )
    conn.commit()
    return {"duplicate": False, "edge_id": cur.lastrowid,
            "source": src["node_id"], "target": tgt["node_id"], "edge_type": edge_type,
            "source_id": src["id"], "target_id": tgt["id"]}


def link_remove(conn: sqlite3.Connection, edge_id: int) -> dict:
    e = conn.execute("SELECT * FROM edges WHERE id = ?", (edge_id,)).fetchone()
    if not e:
        raise ValidationError(f"edge #{edge_id} not found")
    conn.execute("DELETE FROM edges WHERE id = ?", (edge_id,))
    conn.commit()
    orphaned = [n for n in (e["source_node_id"], e["target_node_id"])
                if edge_count(conn, n) == 0]
    return {"edge_id": edge_id, "source": e["source_node_id"],
            "target": e["target_node_id"], "edge_type": e["edge_type"], "orphaned": orphaned}


def supersede(conn: sqlite3.Connection, new_node_id: str, old_ref,
              archive_reason: str) -> dict:
    """Archive the old memory as superseded + create the supersedes edge (new → old)."""
    if not archive_reason or len(archive_reason) < 20:
        raise ValidationError("supersedes requires archive_reason (min 20 chars)")
    old = resolve_ref(conn, old_ref)
    if not old:
        raise ValidationError(f"supersedes ref '{old_ref}' not found")
    link_add(conn, new_node_id, old["node_id"], "supersedes", archive_reason)
    conn.execute(
        "UPDATE memories SET status = 'superseded', archive_reason = ?, updated_at = ? "
        "WHERE node_id = ?",
        (archive_reason, now(), old["node_id"]),
    )
    conn.commit()
    return {"old_id": old["id"], "old_node_id": old["node_id"], "old_title": old["title"]}
