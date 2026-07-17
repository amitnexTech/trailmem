"""Session boundary tracking + the 7-section welcome briefing.

Order is load-bearing: boundary is fetched BEFORE the session row is
registered/updated, and the current session is always excluded — otherwise
"since last session" is always empty.
"""

import sqlite3

from .queries import edge_count
from .store import fmt_local, now

SIGNIFICANT = ("decision", "lesson", "error_pattern", "task", "session_summary", "constraint")


def register_session(conn: sqlite3.Connection, session_id: str, agent_type: str,
                     project: str | None) -> None:
    """Lazy registration on any trailmem_* call. NEVER touches last_welcome_at
    (only welcome writes it — that's the anti-bloat flag). started_at set once."""
    ts = now()
    conn.execute(
        "INSERT INTO sessions (session_id, agent_type, project, started_at, last_seen_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(session_id) DO UPDATE SET last_seen_at = excluded.last_seen_at",
        (session_id, agent_type, project, ts, ts),
    )
    conn.commit()


def welcome(conn: sqlite3.Connection, session_id: str, agent_type: str,
            project: str | None, *, force: bool = False) -> str:
    # Step 1: boundary BEFORE registering, excluding current session.
    boundary = conn.execute(
        "SELECT MAX(started_at) FROM sessions WHERE agent_type = ? AND session_id != ?",
        (agent_type, session_id),
    ).fetchone()[0]

    # Step 2: atomic read-prior + register (BEGIN IMMEDIATE = write lock).
    ts = now()
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute(
        "SELECT last_welcome_at FROM sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    prior_welcome = row["last_welcome_at"] if row else None
    conn.execute(
        "INSERT INTO sessions (session_id, agent_type, project, started_at, last_seen_at, last_welcome_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(session_id) DO UPDATE SET last_seen_at = excluded.last_seen_at, "
        "last_welcome_at = excluded.last_welcome_at",
        (session_id, agent_type, project, ts, ts, ts),
    )
    conn.commit()

    # Step 3: anti-bloat — repeat call gets the short form.
    short = prior_welcome is not None and not force

    scope = "(project = ? OR project IS NULL)"
    out, shown = [], set()

    # Section 1: pinned + constraints, full content, no cap.
    pinned = conn.execute(
        f"SELECT * FROM memories WHERE status = 'active' AND {scope} "
        "AND (pinned = 1 OR event_type = 'constraint') "
        "ORDER BY pinned DESC, created_at DESC",
        (project,),
    ).fetchall()
    if pinned:
        out.append("📌 PINNED + CONSTRAINTS")
        for m in pinned:
            out.append(_full(conn, m))
            shown.add(m["id"])
        if len(pinned) > 10:
            out.append(f"⚠ {len(pinned)} pinned entries — consider unpinning some.")

    stats = _stats_line(conn, project)

    if short:
        out.append("")
        out.append(stats)
        return "\n".join(out) if pinned else stats

    # Section 2: last activity by any agent (significant types).
    last_any = conn.execute(
        f"SELECT * FROM memories WHERE status = 'active' AND {scope} "
        f"AND event_type IN {SIGNIFICANT} ORDER BY created_at DESC LIMIT 5",
        (project,),
    ).fetchall()
    last_any = next((m for m in last_any if m["id"] not in shown), None)
    if last_any:
        out.append("\n🔄 LAST ACTIVITY")
        out.append(_full(conn, last_any))
        shown.add(last_any["id"])

    # Section 3: this agent's last — only if different from section 2.
    last_mine = conn.execute(
        f"SELECT * FROM memories WHERE agent_type = ? AND status = 'active' AND {scope} "
        "ORDER BY created_at DESC LIMIT 1",
        (agent_type, project),
    ).fetchone()
    if last_mine is None:
        out.append(f"\nFirst session for {agent_type} on this project.")
    elif last_mine["id"] not in shown:
        out.append("\n🔄 YOUR LAST")
        out.append(_full(conn, last_mine))
        shown.add(last_mine["id"])

    # Section 4: since boundary (or first-time fallback: last 5 significant).
    if boundary:
        since = conn.execute(
            f"SELECT * FROM memories WHERE created_at > ? AND status = 'active' AND {scope} "
            "ORDER BY created_at DESC",
            (boundary, project),
        ).fetchall()
        since = [m for m in since if m["id"] not in shown]
        if since:
            out.append("\n🆕 SINCE LAST SESSION")
            for m in since:
                if m["pinned"] or m["event_type"] == "constraint":
                    out.append(_full(conn, m))
                else:
                    out.append(_title_line(conn, m))
                shown.add(m["id"])
    else:
        recent = conn.execute(
            f"SELECT * FROM memories WHERE status = 'active' AND {scope} "
            f"AND event_type IN {SIGNIFICANT} ORDER BY created_at DESC LIMIT 5",
            (project,),
        ).fetchall()
        recent = [m for m in recent if m["id"] not in shown]
        if recent:
            out.append("\n🆕 RECENT (first session — no boundary)")
            for m in recent:
                out.append(_title_line(conn, m))
                shown.add(m["id"])

    # Section 5: open tasks, all of them.
    tasks = conn.execute(
        f"SELECT * FROM memories WHERE event_type = 'task' AND status = 'active' AND {scope} "
        "ORDER BY created_at ASC",
        (project,),
    ).fetchall()
    tasks = [m for m in tasks if m["id"] not in shown]
    if tasks:
        out.append("\n⏳ OPEN TASKS")
        for m in tasks:
            out.append(_title_line(conn, m))
            shown.add(m["id"])
        if len(tasks) > 5:
            out.append("Consider resolving older tasks.")

    # Section 6: action needed — only when something is actually pending.
    alerts = _action_needed(conn, project)
    if alerts:
        out.append("\n⚠️ ACTION NEEDED\n⚠ " + ", ".join(alerts))

    # Section 7: stats, always.
    out.append("\n" + stats)
    return "\n".join(out).lstrip("\n")


def _full(conn, m) -> str:
    return (f"#{m['id']} [{m['node_id']}] [↔{edge_count(conn, m['node_id'])}] "
            f"[{m['event_type']}] {m['title']}\n  {m['content']}")


def _title_line(conn, m) -> str:
    return (f"#{m['id']} [{m['node_id']}] [↔{edge_count(conn, m['node_id'])}] "
            f"{m['title']} [{m['agent_type']}, {fmt_local(m['created_at'], date_only=True)}]")


def _action_needed(conn, project) -> list[str]:
    alerts = []
    orphans = conn.execute(
        "SELECT COUNT(*) FROM memories m WHERE m.status = 'active' "
        "AND (m.project = ? OR m.project IS NULL) "
        "AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.source_node_id = m.node_id "
        "OR e.target_node_id = m.node_id)",
        (project,),
    ).fetchone()[0]
    if orphans:
        alerts.append(f"{orphans} orphan{'s' if orphans > 1 else ''} need linking")
    stale = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE event_type = 'task' AND status = 'active' "
        "AND (project = ? OR project IS NULL) "
        "AND created_at < datetime('now', '-7 days')",
        (project,),
    ).fetchone()[0]
    if stale:
        alerts.append(f"{stale} task{'s' if stale > 1 else ''} stale >7d")
    contradictions = conn.execute(
        "SELECT COUNT(*) FROM edges WHERE edge_type = 'contradicts'"
    ).fetchone()[0]
    if contradictions:
        alerts.append(f"{contradictions} contradiction{'s' if contradictions > 1 else ''} unresolved")
    return alerts


def _stats_line(conn, project) -> str:
    mems = conn.execute("SELECT COUNT(*) FROM memories WHERE status = 'active'").fetchone()[0]
    edges = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    orphans = conn.execute(
        "SELECT COUNT(*) FROM memories m WHERE m.status = 'active' "
        "AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.source_node_id = m.node_id "
        "OR e.target_node_id = m.node_id)"
    ).fetchone()[0]
    proj_n = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE status = 'active' AND project = ?", (project,)
    ).fetchone()[0]
    global_n = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE status = 'active' AND project IS NULL"
    ).fetchone()[0]
    name = (project or "global").rstrip("/").rsplit("/", 1)[-1]
    return (f"📊 {mems} memories | {edges} edges | {orphans} orphans | "
            f"Project: {name} ({proj_n} project + {global_n} global)")
