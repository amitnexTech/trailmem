"""trailmem CLI — full command surface per docs/cli.md.

Exit codes: 0 ok, 1 general error, 2 validation, 3 exact duplicate,
4 near-duplicate blocked.
"""

import argparse
import json
import os
import sys

from . import __version__, dashboard, embeddings, integrate, models, ops, queries, sessions
from . import store as store_mod
from .config import CONFIG_PATH, TRAILMEM_HOME, db_path, load_config, save_config
from .schema import connect, has_vec, init_db
from .store import ValidationError


def _conn():
    conn = connect()
    init_db(conn)
    return conn


def _ctx(agent=None):
    agent = store_mod.resolve_agent(agent)
    proj = store_mod.resolve_project(None)
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("KIRO_SESSION_ID")
    return agent, proj, sid


# ---- write ops ----

def cmd_store(a) -> int:
    conn = _conn()
    agent, proj, sid = _ctx(a.agent)
    r = store_mod.store(
        conn, a.content, a.title, a.type, agent_type=agent, work_type=a.work_type,
        project=proj, session_id=sid, source_uri=a.source, modified_files=a.modified_files,
        pinned=a.pin, link_to=a.link_to, edge_type=a.edge_type, force=a.force,
    )
    if r["outcome"] == "rejected_exact":
        d = r["duplicate"]
        print(f"Rejected: exact duplicate of #{d['id']} [{d['node_id']}] '{d['title']}'. "
              f"Use trailmem edit {d['id']}.")
        return 3
    if r["outcome"] == "blocked_near_dup":
        d = r["duplicate"]
        print(f"Blocked: {d['similarity']:.0%} similar to #{d['id']} [{d['node_id']}] "
              f"'{d['title']}'. Edit it or pass --force.")
        return 4
    print(f"Stored #{r['id']} [{r['node_id']}] '{a.title}'.")
    if a.supersedes:
        s = ops.supersede(conn, r["node_id"], a.supersedes, a.archive_reason)
        print(f"Superseded #{s['old_id']} '{s['old_title']}' (archived).")
    if r.get("linked"):
        print(f"Linked to {r['linked']['target']} [{r['linked']['edge_type']}].")
    for w in r.get("warnings", []):
        print(f"⚠ {w}")
    for c in r.get("related_candidates", []):
        print(f"Related candidate: #{c['id']} [{c['node_id']}] ({c['similarity']})")
    return 0


def cmd_edit(a) -> int:
    r = ops.edit(_conn(), a.ref, content=a.content, title=a.title, event_type=a.type,
                 pinned=a.pin, link_to=a.link_to, edge_type=a.edge_type,
                 status=a.status, archive_reason=a.reason)
    print(f"Updated #{r['id']} [{r['node_id']}] '{r['title']}': "
          f"{', '.join(r['changed']) or 'nothing'}.")
    return 0


def cmd_archive(a) -> int:
    r = ops.edit(_conn(), a.ref, status="archived", archive_reason=a.reason,
                 link_to=a.link_to)
    print(f"Archived #{r['id']} [{r['node_id']}] '{r['title']}'. Reason: '{a.reason}'.")
    return 0


def cmd_link(a) -> int:
    r = ops.link_add(_conn(), a.source, a.target, a.type, a.reason or "")
    if r["duplicate"]:
        print(f"Edge already exists: #{r['source_id']} → #{r['target_id']} [{r['edge_type']}].")
    else:
        print(f"Linked #{r['source_id']} → #{r['target_id']} [{r['edge_type']}] (edge e{r['edge_id']}).")
    return 0


def cmd_unlink(a) -> int:
    conn = _conn()
    r = ops.link_remove(conn, a.edge_id)
    print(f"Unlinked edge #{r['edge_id']} ({r['source']} → {r['target']} [{r['edge_type']}]).")
    for n in r["orphaned"]:
        m = queries.resolve_ref(conn, n)
        print(f"⚠ #{m['id']} now has 0 edges (orphan). Consider linking.")
    return 0


def _set_pin(ref, value) -> int:
    r = ops.edit(_conn(), ref, pinned=value)
    print(f"{'Pinned' if value else 'Unpinned'} #{r['id']} [{r['node_id']}] '{r['title']}'.")
    return 0


def cmd_delete(a) -> int:
    conn = _conn()
    m = queries.resolve_ref(conn, a.ref)
    if not m:
        raise ValidationError(f"ref '{a.ref}' not found")
    if not a.confirm:
        print("Refusing: hard delete needs --confirm. (Prefer trailmem archive — keeps the trail.)")
        return 1
    node_id = m["node_id"]
    # Virtual tables do NOT cascade — all three explicitly. Edges cascade via FK.
    conn.execute("DELETE FROM memories WHERE node_id = ?", (node_id,))
    conn.execute("DELETE FROM memories_fts WHERE node_id = ?", (node_id,))
    if has_vec(conn):
        try:
            conn.execute("DELETE FROM memories_vec WHERE node_id = ?", (node_id,))
        except Exception:
            pass
    conn.commit()
    print(f"Deleted #{m['id']} [{node_id}] '{m['title']}' permanently (edges cascaded).")
    return 0


# ---- read ops ----

def cmd_show(a) -> int:
    data = queries.show(_conn(), a.ref)
    if not data:
        raise ValidationError(f"ref '{a.ref}' not found")
    print(queries.format_show(data))
    return 0


def cmd_query(a) -> int:
    _, proj, _ = _ctx()
    results = queries.query(_conn(), a.text, type_filter=a.type, agent_filter=a.agent,
                            project=proj, limit=a.limit)
    if a.format == "json":
        print(json.dumps(results, indent=2, default=str))
    else:
        print(queries.format_query_results(results, a.text))
    return 0


def cmd_welcome(a) -> int:
    agent, proj, sid = _ctx(a.agent)
    sid = sid or f"cli-{os.getppid()}"
    print(sessions.welcome(_conn(), sid, agent, proj, force=a.force))
    return 0


def cmd_similar(a) -> int:
    import hashlib
    conn = _conn()
    _, proj, _ = _ctx()
    h = hashlib.sha256(a.content.encode()).hexdigest()
    dup = conn.execute(
        "SELECT id, node_id, title FROM memories WHERE content_hash = ? AND project IS ?",
        (h, proj)).fetchone()
    if dup:
        print(f"exact: #{dup['id']} [{dup['node_id']}] '{dup['title']}'")
        return 0
    vec = embeddings.embed(a.content)
    if vec is None:
        print("(embeddings unavailable — only exact-hash checked; no exact match)")
        return 0
    cfg = load_config()["embedding"]
    for n in store_mod._similar(conn, vec):
        band = ("0.92+" if n["similarity"] > cfg["dedup_block"]
                else "0.85+" if n["similarity"] >= cfg["dedup_warn"] else "low")
        print(f"{band}: #{n['id']} [{n['node_id']}] '{n['title']}' ({n['similarity']:.2f})")
    return 0


def cmd_list(a) -> int:
    conn = _conn()
    _, proj, _ = _ctx()
    where, params, order, limit = ["1=1"], [], "created_at DESC", None
    if a.archived:
        where.append("status != 'active'")
    else:
        where.append("status = 'active'")
    if a.pinned:
        where.append("(pinned = 1 OR event_type = 'constraint')")
    if a.tasks:
        where.append("event_type = 'task'")
    if a.by_agent:
        where.append("agent_type = ?"); params.append(a.by_agent)
    if getattr(a, "global"):
        where.append("project IS NULL")
    elif a.project:
        where.append("project = ?"); params.append(a.project)
    else:
        where.append("(project = ? OR project IS NULL)"); params.append(proj)
    if a.recent:
        limit = 10
    rows = conn.execute(
        f"SELECT * FROM memories WHERE {' AND '.join(where)} ORDER BY {order}"
        + (f" LIMIT {limit}" if limit else ""), params).fetchall()
    if a.orphans:
        rows = [m for m in rows if queries.edge_count(conn, m["node_id"]) == 0]

    if a.format == "json":
        print(json.dumps([dict(m) for m in rows], indent=2, default=str))
        return 0
    day = None
    for m in rows:
        if a.timeline and m["created_at"][:10] != day:
            day = m["created_at"][:10]
            print(f"\n== {day} ==")
        n = queries.edge_count(conn, m["node_id"])
        tag = "" if m["status"] == "active" else f" [{m['status']}]"
        print(f"#{m['id']} [{m['node_id']}] [{m['event_type']}] [{m['agent_type']}]"
              f"{tag} [↔{n}] {m['title']}")
    if not rows:
        print("(no memories)")
    return 0


def cmd_stats(a) -> int:
    conn = _conn()
    _, proj, _ = _ctx()
    print(sessions._stats_line(conn, proj))
    print(f"DB: {db_path()} ({db_path().stat().st_size // 1024} KB)")
    for row in conn.execute(
            "SELECT event_type, COUNT(*) c FROM memories WHERE status='active' "
            "GROUP BY event_type ORDER BY c DESC"):
        print(f"  {row['event_type']}: {row['c']}")
    return 0


def cmd_dashboard(a) -> int:
    """Start the first-party local dashboard; it is never a public listener."""
    if not 1 <= a.port <= 65535:
        raise ValidationError("dashboard port must be between 1 and 65535")
    project = "all" if a.project in (None, "all") else store_mod.resolve_project(a.project)
    agent = store_mod.resolve_agent(a.agent or "user")
    return dashboard.serve(port=a.port, project=project, default_agent=agent)


# ---- admin ----

def cmd_maintain(a) -> int:
    conn = _conn()
    old = conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE last_seen_at < datetime('now', '-90 days')"
    ).fetchone()[0]
    orphans = conn.execute(
        "SELECT COUNT(*) FROM memories m WHERE m.status='active' AND NOT EXISTS "
        "(SELECT 1 FROM edges e WHERE e.source_node_id=m.node_id OR e.target_node_id=m.node_id)"
    ).fetchone()[0]
    print(f"sessions >90 days old: {old}")
    print(f"orphan memories (report only, never auto-archived): {orphans}")
    if not a.apply:
        print("(dry-run — pass --apply to purge old sessions)")
        return 0
    if old and input(f"Purge {old} old sessions? [y/N] ").lower() != "y":
        print("aborted")
        return 0
    conn.execute("DELETE FROM sessions WHERE last_seen_at < datetime('now', '-90 days')")
    conn.commit()
    print(f"purged {old} sessions")
    return 0


def cmd_export(a) -> int:
    conn = _conn()
    data = {
        "trailmem_export": 1,
        "memories": [dict(r) for r in conn.execute("SELECT * FROM memories")],
        "edges": [dict(r) for r in conn.execute("SELECT * FROM edges")],
    }
    out = json.dumps(data, indent=2, default=str)
    if a.output:
        with open(a.output, "w") as f:
            f.write(out)
        print(f"exported {len(data['memories'])} memories + {len(data['edges'])} edges → {a.output}")
    else:
        print(out)
    return 0


def cmd_import(a) -> int:
    conn = _conn()
    with open(a.file) as f:
        data = json.load(f)
    if "trailmem_export" not in data:
        raise ValidationError(f"{a.file} is not a trailmem export")
    if a.replace:
        if input("REPLACE wipes the entire DB. Type 'replace' to confirm: ") != "replace":
            print("aborted")
            return 0
        if input("Really? Type 'yes': ") != "yes":
            print("aborted")
            return 0
        for t in ("edges", "memories", "memories_fts"):
            conn.execute(f"DELETE FROM {t}")
        if has_vec(conn):
            try:
                conn.execute("DELETE FROM memories_vec")
            except Exception:
                pass
    added = 0
    for m in data["memories"]:
        if conn.execute("SELECT 1 FROM memories WHERE node_id=?", (m["node_id"],)).fetchone():
            continue
        cols = [k for k in m if k != "id"]
        conn.execute(
            f"INSERT INTO memories ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            [m[c] for c in cols])
        conn.execute("INSERT INTO memories_fts (node_id, title, content) VALUES (?,?,?)",
                     (m["node_id"], m["title"], m["content"]))
        added += 1
    for e in data["edges"]:
        conn.execute(
            "INSERT OR IGNORE INTO edges (source_node_id, target_node_id, edge_type, "
            "weight, metadata, created_at) VALUES (?,?,?,?,?,?)",
            (e["source_node_id"], e["target_node_id"], e["edge_type"],
             e.get("weight", 1.0), e.get("metadata"), e["created_at"]))
    conn.commit()
    print(f"imported {added} new memories. Run `trailmem reindex` to embed them.")
    return 0


def cmd_setup(a) -> int:
    TRAILMEM_HOME.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        save_config(load_config())
        print(f"wrote {CONFIG_PATH}")
    conn = _conn()
    conn.close()
    print(f"database ready: {db_path()}")
    cfg = load_config()["embedding"]
    if cfg["enabled"] and not models.installed(cfg["model"]):
        print(f"downloading default embedding model '{cfg['model']}' ...")
        if models.install(cfg["model"]) != 0:
            print("⚠ model download failed — trailmem works in FTS-only mode until "
                  f"`trailmem model install {cfg['model']}` succeeds.")
    print("MCP registration: run `trailmem integrate` (auto-detect, asks first), or manually →")
    print('  claude mcp add trailmem -- trailmem-mcp')
    return 0


def cmd_integrate(a) -> int:
    return integrate.run()


def cmd_doctor(a) -> int:
    ok = True
    cfg = load_config()
    print(f"home:   {TRAILMEM_HOME} {'✓' if TRAILMEM_HOME.exists() else '✗ (run trailmem setup)'}")
    print(f"config: {CONFIG_PATH} {'✓' if CONFIG_PATH.exists() else '✗'}")
    if not db_path().exists():
        print(f"db:     {db_path()} ✗ (run trailmem setup)")
        return 1
    conn = connect()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("memories", "edges", "sessions", "memories_fts"):
        present = t in tables
        ok &= present
        print(f"table:  {t} {'✓' if present else '✗'}")
    emb = cfg["embedding"]
    if emb["enabled"]:
        vec = has_vec(conn)
        print(f"vec:    sqlite-vec {'✓' if vec else '✗ DEGRADED — FTS-only, near-dup detection OFF'}")
        got = models.installed(emb["model"])
        print(f"model:  {emb['model']} ({emb['dimensions']}d) "
              f"{'✓' if got else '✗ not installed — run: trailmem model install ' + emb['model']}")
        ok &= vec and got
    else:
        print("vec:    disabled by config — FTS-only mode (exact-hash dedup only)")
    conn.close()
    return 0 if ok else 1


def cmd_model(a) -> int:
    if a.model_cmd == "list":
        cfg = load_config()["embedding"]
        for name, spec in models.REGISTRY.items():
            mark = " (active)" if cfg["enabled"] and cfg["model"] == name else ""
            state = "installed" if models.installed(name) else "available"
            print(f"{name}: {state}{mark} — {spec['note']}")
        if not cfg["enabled"]:
            print("(embeddings disabled — FTS-only mode)")
        return 0
    if a.model_cmd == "install":
        # Default name differs by mode: registry download → default model;
        # custom --path → 'custom', never a registry name (overwrite guard).
        return models.install(a.name or ("custom" if a.path else "bge-small"), a.path)
    if a.model_cmd == "use":
        return models.use(a.name)
    if a.model_cmd == "disable":
        return models.disable()
    return 1


def cmd_reindex(a) -> int:
    return models.reindex(_conn())


# ---- statusline (never fail the host; read-only COUNT) ----

def cmd_statusline(a) -> int:
    """One-line status for a host statusline: how many memories this session
    stored. Reads session_id from stdin JSON (Claude Code) or the usual env
    vars. Prints nothing-noisy on failure — a statusline must never break."""
    try:
        sid = None
        if not sys.stdin.isatty():
            import json
            try:
                sid = json.loads(sys.stdin.read() or "{}").get("session_id")
            except Exception:
                sid = None
        sid = sid or os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("KIRO_SESSION_ID")
        if not sid:
            return 0  # no session context → say nothing
        n = _conn().execute(
            "SELECT COUNT(*) FROM memories WHERE session_id = ?", (sid,)
        ).fetchone()[0]
        if n > 0:
            print(f"🧠 trailmem: {n} saved this session")
        else:
            print("⚠ trailmem: 0 saved this session · /tm-save before exit")
    except Exception:
        pass  # statusline must never emit an error or block the host
    return 0


# ---- hooks (never fail the host session) ----

def cmd_hook(a) -> int:
    import traceback
    try:
        agent, proj, sid = _ctx(a.agent)
    except ValidationError:
        agent, proj, sid = None, os.getcwd(), None
    try:
        if a.event == "session-start":
            conn = _conn()
            if agent and sid:
                print(sessions.welcome(conn, sid, agent, proj))
            elif agent:  # session-less mode: welcome without boundary tracking
                print(sessions.welcome(conn, f"adhoc-{os.getppid()}", agent, proj))
        elif a.event == "session-stop":
            if sid:
                conn = _conn()
                conn.execute("UPDATE sessions SET last_seen_at = ? WHERE session_id = ?",
                             (store_mod.now(), sid))
                conn.commit()
    except Exception:
        try:
            log = TRAILMEM_HOME / "hooks.log"
            TRAILMEM_HOME.mkdir(parents=True, exist_ok=True)
            with open(log, "a") as f:
                f.write(f"--- {store_mod.now()} {a.event}\n{traceback.format_exc()}\n")
        except Exception:
            pass
        print(f"trailmem hook {a.event} failed (see ~/.trailmem/hooks.log)", file=sys.stderr)
    return 0  # ALWAYS — a memory failure must never block the agent


# ---- parser ----

def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="trailmem",
        description="Graph-linked persistent memory for AI coding agents",
        epilog=(
            "examples:\n"
            "  trailmem setup                            first-time setup (config, DB, model)\n"
            "  trailmem doctor                           health check\n"
            "  trailmem store --title \"Note\" --type lesson --agent user \"content here\"\n"
            "  trailmem query \"what did I learn about X\"\n"
            "  trailmem list                             list stored memories\n"
            "  trailmem <command> --help                 help for any command\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"trailmem {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("store", help="Store a new memory")
    s.add_argument("content")
    s.add_argument("--title", required=True)
    s.add_argument("--type", required=True)
    s.add_argument("--agent")
    s.add_argument("--work-type")
    s.add_argument("--source")
    s.add_argument("--modified-files")
    s.add_argument("--pin", action="store_true")
    s.add_argument("--link-to")
    s.add_argument("--edge-type", default="related")
    s.add_argument("--supersedes")
    s.add_argument("--archive-reason")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_store)

    s = sub.add_parser("edit", help="Edit a memory")
    s.add_argument("ref")
    s.add_argument("--content")
    s.add_argument("--title")
    s.add_argument("--type")
    s.add_argument("--pin", action=argparse.BooleanOptionalAction)
    s.add_argument("--status", choices=["archived", "superseded"])
    s.add_argument("--reason")
    s.add_argument("--link-to")
    s.add_argument("--edge-type", default="related")
    s.set_defaults(func=cmd_edit)

    s = sub.add_parser("archive", help="Archive a memory (preserves trail)")
    s.add_argument("ref")
    s.add_argument("--reason", required=True)
    s.add_argument("--link-to")
    s.set_defaults(func=cmd_archive)

    s = sub.add_parser("link", help="Link two memories")
    s.add_argument("source")
    s.add_argument("target")
    s.add_argument("--type", default="related")
    s.add_argument("--reason")
    s.set_defaults(func=cmd_link)

    s = sub.add_parser("unlink", help="Remove an edge")
    s.add_argument("edge_id", type=int)
    s.set_defaults(func=cmd_unlink)

    s = sub.add_parser("pin", help="Pin a memory")
    s.add_argument("ref")
    s.set_defaults(func=lambda a: _set_pin(a.ref, True))
    s = sub.add_parser("unpin", help="Unpin a memory")
    s.add_argument("ref")
    s.set_defaults(func=lambda a: _set_pin(a.ref, False))

    s = sub.add_parser("show", help="Show one memory in full (+ edges)")
    s.add_argument("ref")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("query", help="Search memories")
    s.add_argument("text")
    s.add_argument("--type")
    s.add_argument("--agent")
    s.add_argument("--limit", type=int, default=5)
    s.add_argument("--format", choices=["text", "json"], default="text")
    s.set_defaults(func=cmd_query)

    s = sub.add_parser("welcome", help="Session briefing")
    s.add_argument("--agent")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_welcome)

    s = sub.add_parser("similar", help="Check near-duplicates before storing")
    s.add_argument("content")
    s.set_defaults(func=cmd_similar)

    s = sub.add_parser("list", help="List memories (filterable)")
    for flag in ("recent", "orphans", "pinned", "tasks", "timeline", "archived"):
        s.add_argument(f"--{flag}", action="store_true")
    s.add_argument("--by-agent")
    s.add_argument("--project")
    s.add_argument("--global", action="store_true")
    s.add_argument("--format", choices=["text", "json"], default="text")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("stats", help="Statistics")
    s.set_defaults(func=cmd_stats)

    s = sub.add_parser("dashboard", help="Start the loopback-only local dashboard")
    s.add_argument("--port", type=int, default=3800)
    s.add_argument("--project", help="Project scope: 'all' (default, global + every project), 'global', or a project path")
    s.add_argument("--agent", help="Default attribution for dashboard-created memories")
    s.set_defaults(func=cmd_dashboard)

    s = sub.add_parser("maintain", help="Maintenance (dry-run by default)")
    s.add_argument("--apply", action="store_true")
    s.set_defaults(func=cmd_maintain)

    s = sub.add_parser("export", help="Full DB dump to JSON")
    s.add_argument("output", nargs="?")
    s.add_argument("--format", choices=["json"], default="json")
    s.set_defaults(func=cmd_export)

    s = sub.add_parser("import", help="Import from a trailmem export")
    s.add_argument("file")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--merge", action="store_true")
    g.add_argument("--replace", action="store_true")
    s.set_defaults(func=cmd_import)

    s = sub.add_parser("setup", help="Create ~/.trailmem/, init DB, download model")
    s.set_defaults(func=cmd_setup)
    s = sub.add_parser("integrate", help="Register MCP with detected agents (asks first)")
    s.set_defaults(func=cmd_integrate)
    s = sub.add_parser("doctor", help="Health check")
    s.set_defaults(func=cmd_doctor)

    s = sub.add_parser("model", help="Embedding model management")
    ms = s.add_subparsers(dest="model_cmd", required=True)
    ms.add_parser("list")
    mi = ms.add_parser("install")
    mi.add_argument("name", nargs="?")
    mi.add_argument("--path")
    mu = ms.add_parser("use")
    mu.add_argument("name")
    ms.add_parser("disable")
    s.set_defaults(func=cmd_model)

    s = sub.add_parser("reindex", help="Re-embed all memories with the active model")
    s.set_defaults(func=cmd_reindex)

    s = sub.add_parser("delete", help="Hard delete (NOT recommended — use archive)")
    s.add_argument("ref")
    s.add_argument("--hard", action="store_true")
    s.add_argument("--confirm", action="store_true")
    s.set_defaults(func=cmd_delete)

    s = sub.add_parser("statusline", help="One-line session status for a host statusline")
    s.set_defaults(func=cmd_statusline)

    s = sub.add_parser("hook", help="Host lifecycle hooks (always exit 0)")
    s.add_argument("event", choices=["session-start", "session-stop"])
    s.add_argument("--agent")
    s.set_defaults(func=cmd_hook)

    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("help", "--help", "-h"):
        p.print_help()
        return 0
    args = p.parse_args(argv)
    try:
        return args.func(args)
    except ValidationError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
