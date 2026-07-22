"""Store pipeline: validate → auto-fill → dedup bands → insert (all three tables) → link assistance.

Returns a structured outcome dict; MCP/CLI layers format it. Duplicate hits are
business outcomes, not exceptions — only validation failures raise.
"""

import hashlib
import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone

from . import embeddings
from .config import load_config
from .errors import ValidationError
from .identity import (
    SessionContext,
    resolve_agent,
    resolve_project,
    session_key,
)
from .schema import has_vec

EVENT_TYPES = {
    "decision", "lesson", "error_pattern", "task",
    "memory", "user_preference", "constraint", "session_summary",
}
WORK_TYPES = {"discussion", "file-edit", "code-written", "bug-fix", "research", "setup", "review"}
EDGE_TYPES = {"related", "derived_from", "supersedes", "contradicts", "evolves"}

_STOPWORDS = {"the", "is", "of", "and", "to", "a", "in", "for", "on", "with", "it", "this", "that"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fmt_local(ts: str | None, *, date_only: bool = False) -> str:
    """Render a stored UTC ISO timestamp in the system's local timezone.

    Storage is UTC (spec-locked); this is display-only so a memory made at
    03:35 UTC reads as 09:05 local instead of appearing on the wrong day.
    """
    if not ts:
        return ""
    dt = datetime.fromisoformat(ts).astimezone()
    if date_only:
        return dt.strftime("%Y-%m-%d")
    return dt.strftime("%Y-%m-%d %H:%M %Z")

def session_id_from_env(env=os.environ) -> str | None:
    """Backward-compatible generic env helper; native envs belong to adapters."""
    return env.get("TRAILMEM_SESSION_ID")


def session_id_from_payload(payload: dict) -> str | None:
    """Backward-compatible canonical payload helper."""
    value = payload.get("session_id") if isinstance(payload, dict) else None
    return str(value) if value else None


def english_warning(content: str) -> str | None:
    """Soft heuristic: ASCII ratio catches Devanagari/CJK; stopword density
    catches Roman-script Hinglish. Warn only, never block."""
    ascii_ratio = sum(c.isascii() for c in content) / max(len(content), 1)
    if ascii_ratio < 0.8:
        return "content looks non-English (non-ASCII heavy) — store the English version for search reliability"
    words = re.findall(r"[a-z']+", content.lower())
    if len(words) >= 15 and sum(w in _STOPWORDS for w in words) / len(words) < 0.03:
        return "content may not be English (low stopword density) — store the English version for search reliability"
    return None


def validate(title: str, content: str, event_type: str, work_type: str | None) -> list[str]:
    """Hard-reject on structural problems; return soft warnings."""
    if not title or not (3 <= len(title) <= 60):
        raise ValidationError(f"title must be 3-60 chars (got {len(title or '')})")
    if not content or len(content) < 50:
        raise ValidationError(f"content must be >=50 chars (got {len(content or '')}) — a memory this short has no recall value")
    if event_type not in EVENT_TYPES:
        raise ValidationError(f"event_type must be one of {sorted(EVENT_TYPES)}")
    if work_type is not None and work_type not in WORK_TYPES:
        raise ValidationError(f"work_type must be one of {sorted(WORK_TYPES)}")
    warnings = []
    if len(content) > 4000:
        warnings.append(f"content is {len(content)} chars (>4000) — consider splitting")
    w = english_warning(content)
    if w:
        warnings.append(w)
    return warnings


def resolve_files(value: str | None, field: str) -> str | None:
    """code_files/doc_files are required: comma-separated paths, or the literal
    'none' (normalised to NULL) when nothing of that kind was touched. Missing
    is rejected — half the DB had empty file fields because agents skipped the
    optional param, making "no files" indistinguishable from "forgot"."""
    v = (value or "").strip()
    if not v:
        raise ValidationError(
            f"{field} is required — pass comma-separated file paths, or 'none' "
            f"if this memory touches no {field.replace('_files', '')} files.")
    return None if v.lower() == "none" else v


def _similar(conn: sqlite3.Connection, vec, limit: int = 3) -> list[dict]:
    """Top-N nearest neighbours, active-first. vec0 KNN demands a bare
    ORDER BY distance, so the active-first reorder happens in Python."""
    rows = conn.execute(
        "SELECT node_id, (1.0 - distance) AS similarity FROM memories_vec "
        "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
        (vec.tobytes(), limit * 3),
    ).fetchall()
    out = []
    for r in rows:
        m = conn.execute(
            "SELECT id, title, status FROM memories WHERE node_id = ?", (r["node_id"],)
        ).fetchone()
        if m:
            out.append({"node_id": r["node_id"], "similarity": r["similarity"],
                        "id": m["id"], "title": m["title"], "status": m["status"]})
    out.sort(key=lambda n: (n["status"] != "active", -n["similarity"]))
    return out[:limit]


def store(
    conn: sqlite3.Connection,
    content: str,
    title: str,
    event_type: str = "memory",
    *,
    agent_type: str | None = None,
    work_type: str | None = None,
    project: str | None = None,
    session_id: str | None = None,
    source_uri: str | None = None,
    code_files: str | None = None,
    doc_files: str | None = None,
    pinned: bool = False,
    link_to: str | None = None,
    edge_type: str | None = None,
    force: bool = False,
    context: SessionContext | None = None,
    env=os.environ,
) -> dict:
    warnings = validate(title, content, event_type, work_type)
    code_files = resolve_files(code_files, "code_files")
    doc_files = resolve_files(doc_files, "doc_files")
    agent = context.agent_type if context else resolve_agent(agent_type, env)
    if context:
        project = context.project
    else:
        project = resolve_project(project, env)
    session_id = context.key if context else session_key(
        agent, session_id or session_id_from_env(env))
    if event_type == "user_preference":
        # Singleton: always global, exactly one active record. Architectural
        # rule — force=true does not bypass it (unlike similarity dedup).
        project = None
        existing = conn.execute(
            "SELECT id, node_id, title FROM memories WHERE event_type = 'user_preference' "
            "AND status = 'active' AND project IS NULL",
        ).fetchone()
        if existing:
            return {
                "outcome": "blocked_singleton",
                "duplicate": {"id": existing["id"], "node_id": existing["node_id"],
                              "title": existing["title"]},
                "message": f"user_preference is a singleton — active record "
                           f"#{existing['id']} [{existing['node_id']}] "
                           f"'{existing['title']}' already exists. "
                           f"Use edit(id={existing['id']}) to merge into it; "
                           f"force=true does not bypass this.",
            }
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    cfg = load_config()["embedding"]

    # Band 1: exact hash within project scope (cheap, first, no model).
    dup = conn.execute(
        "SELECT id, node_id, title FROM memories WHERE content_hash = ? AND project IS ?",
        (content_hash, project),
    ).fetchone()
    if dup:
        return {
            "outcome": "rejected_exact",
            "duplicate": {"id": dup["id"], "node_id": dup["node_id"], "title": dup["title"]},
            "message": f"Exact duplicate of #{dup['id']} [{dup['node_id']}] '{dup['title']}'. "
                       f"Use edit(id={dup['id']}) to update, or change content.",
        }

    # Band 2-4: embedding similarity (skipped in FTS-only mode).
    vec = embeddings.embed(content)
    if vec is not None and not has_vec(conn):
        # sqlite-vec extension failed to load on this connection → the
        # memories_vec table is absent; touching it would kill the store.
        vec = None
    neighbours = _similar(conn, vec) if vec is not None else []
    top = neighbours[0] if neighbours else None
    if top and top["similarity"] > cfg["dedup_block"] and not force:
        return {
            "outcome": "blocked_near_dup",
            "duplicate": {"id": top["id"], "node_id": top["node_id"], "title": top["title"],
                          "similarity": round(top["similarity"], 2)},
            "message": f"Near-duplicate of #{top['id']} [{top['node_id']}] '{top['title']}' "
                       f"({top['similarity']:.0%} similar). Suggested: edit(id={top['id']}) "
                       f"to update existing. Or pass force=true to store anyway.",
        }
    if top and cfg["dedup_warn"] <= top["similarity"] <= cfg["dedup_block"]:
        warnings.append(
            f"Similar to #{top['id']} [{top['node_id']}] '{top['title']}' "
            f"({top['similarity']:.0%}). Consider: link(type='related')."
        )

    node_id = f"mem-{secrets.token_hex(4)}"
    ts = now()
    if event_type == "constraint":
        pinned = True  # constraints are always pinned
    cur = conn.execute(
        "INSERT INTO memories (node_id, title, content, event_type, work_type, agent_type, "
        "project, session_id, source_uri, code_files, doc_files, pinned, created_at, content_hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (node_id, title, content, event_type, work_type, agent, project, session_id,
         source_uri, code_files, doc_files, int(pinned), ts, content_hash),
    )
    mem_id = cur.lastrowid
    conn.execute(
        "INSERT INTO memories_fts (node_id, title, content) VALUES (?, ?, ?)",
        (node_id, title, content),
    )
    if vec is not None:
        conn.execute(
            "INSERT INTO memories_vec (node_id, embedding) VALUES (?, ?)",
            (node_id, vec.tobytes()),
        )

    result = {"outcome": "stored", "id": mem_id, "node_id": node_id, "warnings": warnings}

    if link_to:
        et = edge_type or "related"
        if et not in EDGE_TYPES:
            raise ValidationError(f"edge_type must be one of {sorted(EDGE_TYPES)}")
        target = conn.execute(
            "SELECT node_id FROM memories WHERE node_id = ? OR id = ?",
            (link_to, link_to if str(link_to).isdigit() else -1),
        ).fetchone()
        if target:
            conn.execute(
                "INSERT OR IGNORE INTO edges (source_node_id, target_node_id, edge_type, created_at) "
                "VALUES (?, ?, ?, ?)",
                (node_id, target["node_id"], et, ts),
            )
            result["linked"] = {"target": target["node_id"], "edge_type": et}
        else:
            warnings.append(f"link_to '{link_to}' not found — no edge created")

    # Store-time link assistance: candidates on every store, orphan warning below floor.
    candidates = [
        {"id": n["id"], "node_id": n["node_id"], "title": n["title"],
         "similarity": round(n["similarity"], 2)}
        for n in neighbours if n["similarity"] >= 0.3
    ]
    if vec is not None:
        result["related_candidates"] = candidates
        if not candidates and not link_to:
            warnings.append("No related memories found — this is an orphan. Link it or confirm it is standalone.")

    if session_id:
        conn.execute(
            "UPDATE sessions SET write_count = COALESCE(write_count, 0) + 1, "
            "last_write_at = ?, last_seen_at = ? WHERE session_id = ?",
            (ts, ts, session_id),
        )
    conn.commit()
    return result
