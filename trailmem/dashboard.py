"""Loopback-only dashboard for browsing and maintaining Trailmem memories.

The module deliberately uses only the Python standard library.  Browser writes
are delegated to ``store`` and ``ops`` so the dashboard shares the CLI/MCP
validation, deduplication, FTS/vector syncing, archive checks, and link rules.
"""

from __future__ import annotations

import json
import re
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from . import ops, queries, store
from .config import load_config
from .schema import connect, has_vec, init_db
from .store import ValidationError


EVENT_HISTORY_LIMIT = 1_000


class DashboardApp:
    """Application/service boundary for the dashboard HTTP transport."""

    def __init__(self, project: str | None, default_agent: str):
        # project can be: None (global only), "all" (global + every project),
        # or an explicit project path. Mutated at runtime by set_scope().
        self.project = project
        self.default_agent = default_agent

    def connection(self):
        conn = connect()
        init_db(conn)
        return conn

    def set_scope(self, scope: str | None) -> None:
        """Switch scope at runtime without restarting the server."""
        if scope in ("", "global"):
            self.project = None
        else:
            self.project = scope  # "all" or a project path

    def _scope_clause(self, alias: str = "m") -> tuple[str, list[Any]]:
        if self.project is None:
            return f"{alias}.project IS NULL", []
        if self.project == "all":
            return "1=1", []  # global + every project
        return f"({alias}.project = ? OR {alias}.project IS NULL)", [self.project]

    def _in_scope(self, memory: dict[str, Any]) -> bool:
        if self.project == "all":
            return True
        return memory["project"] is None or memory["project"] == self.project

    def scope_label(self) -> str:
        if self.project is None:
            return "global"
        if self.project == "all":
            return "all projects"
        return self.project

    @staticmethod
    def _preview(content: str, limit: int = 180) -> str:
        clean = " ".join(content.split())
        return clean if len(clean) <= limit else f"{clean[:limit - 1]}…"

    def _summary(self, conn, memory: dict[str, Any]) -> dict[str, Any]:
        count = queries.edge_count(conn, memory["node_id"])
        return {
            "id": memory["id"],
            "node_id": memory["node_id"],
            "title": memory["title"],
            "type": memory["event_type"],
            "agent": memory["agent_type"],
            "project": memory["project"],
            "scope": "global" if memory["project"] is None else "project",
            "pinned": bool(memory["pinned"]),
            "status": memory["status"],
            "created_at": memory["created_at"],
            "updated_at": memory["updated_at"],
            "edge_count": count,
            "preview": self._preview(memory["content"]),
        }

    def memory_summary(self, ref: str) -> dict[str, Any] | None:
        conn = self.connection()
        try:
            memory = queries.resolve_ref(conn, ref)
            if not memory or not self._in_scope(dict(memory)):
                return None
            return self._summary(conn, dict(memory))
        finally:
            conn.close()

    def search(self, text: str) -> list[dict[str, Any]]:
        """Return compact, scope-aware results for title/content/ID searches."""
        text = text.strip()
        if not text:
            return []
        conn = self.connection()
        try:
            scope, params = self._scope_clause()
            matches: dict[str, dict[str, Any]] = {}

            # IDs and node IDs are navigational shortcuts, not FTS terms.
            ref = text.lstrip("#")
            if ref.isdigit() or ref.startswith("mem-"):
                memory = queries.resolve_ref(conn, ref)
                if memory and self._in_scope(dict(memory)):
                    matches[memory["node_id"]] = self._summary(conn, dict(memory))

            tokens = re.findall(r"\w+", text)
            if tokens:
                fts_query = " OR ".join(f'"{token}"*' for token in tokens)
                rows = conn.execute(
                    "SELECT m.* FROM memories_fts JOIN memories m "
                    "ON m.node_id = memories_fts.node_id "
                    "WHERE memories_fts MATCH ? AND " + scope
                    + " ORDER BY bm25(memories_fts) LIMIT 100",
                    [fts_query, *params],
                ).fetchall()
                for row in rows:
                    memory = dict(row)
                    matches[memory["node_id"]] = self._summary(conn, memory)

            # Prefix/substring fallback makes partially typed node IDs and
            # titles discoverable even before a full FTS term is available.
            like = f"%{text.lower()}%"
            rows = conn.execute(
                "SELECT m.* FROM memories m WHERE " + scope
                + " AND (LOWER(m.title) LIKE ? OR LOWER(m.node_id) LIKE ?) "
                "ORDER BY m.created_at DESC LIMIT 100",
                [*params, like, like],
            ).fetchall()
            for row in rows:
                memory = dict(row)
                matches.setdefault(memory["node_id"], self._summary(conn, memory))
            return list(matches.values())[:100]
        finally:
            conn.close()

    def snapshot(self) -> dict[str, Any]:
        conn = self.connection()
        try:
            scope, params = self._scope_clause()
            rows = conn.execute(
                "SELECT m.* FROM memories m WHERE " + scope
                + " ORDER BY m.pinned DESC, m.created_at DESC",
                params,
            ).fetchall()
            nodes = [self._summary(conn, dict(row)) for row in rows]
            node_ids = {node["node_id"] for node in nodes}
            edges = []
            for edge in conn.execute("SELECT * FROM edges ORDER BY id").fetchall():
                item = dict(edge)
                if item["source_node_id"] in node_ids and item["target_node_id"] in node_ids:
                    edges.append(
                        {
                            "id": item["id"],
                            "source": item["source_node_id"],
                            "target": item["target_node_id"],
                            "type": item["edge_type"],
                            "reason": item["metadata"] or "",
                        }
                    )
            return {
                "revision": self.revision(conn),
                "project": self.project,
                "scope_label": self.scope_label(),
                "default_agent": self.default_agent,
                "nodes": nodes,
                "edges": edges,
                "stats": self.stats(conn),
            }
        finally:
            conn.close()

    def detail(self, ref: str) -> dict[str, Any] | None:
        conn = self.connection()
        try:
            # queries.show is the canonical full-memory presentation and is the
            # only read surface that constructs the supersession chain.
            detail = queries.show(conn, ref)
            if not detail or not self._in_scope(detail["memory"]):
                return None
            return detail
        finally:
            conn.close()

    def stats(self, conn=None) -> dict[str, Any]:
        owned_conn = conn is None
        conn = conn or self.connection()
        try:
            scope, params = self._scope_clause()
            active = conn.execute(
                "SELECT COUNT(*) FROM memories m WHERE " + scope + " AND m.status = 'active'",
                params,
            ).fetchone()[0]
            orphans = conn.execute(
                "SELECT COUNT(*) FROM memories m WHERE " + scope
                + " AND m.status = 'active' AND NOT EXISTS ("
                "SELECT 1 FROM edges e WHERE e.source_node_id = m.node_id "
                "OR e.target_node_id = m.node_id)",
                params,
            ).fetchone()[0]
            stale_tasks = conn.execute(
                "SELECT COUNT(*) FROM memories m WHERE " + scope
                + " AND m.status = 'active' AND m.event_type = 'task' "
                "AND m.created_at < datetime('now', '-30 days')",
                params,
            ).fetchone()[0]
            contradictions = conn.execute(
                "SELECT COUNT(*) FROM edges e JOIN memories m ON m.node_id = e.source_node_id "
                "WHERE e.edge_type = 'contradicts' AND " + scope,
                params,
            ).fetchone()[0]
            config = load_config()["embedding"]
            vector_ready = bool(config["enabled"] and has_vec(conn))
            return {
                "active": active,
                "orphans": orphans,
                "stale_tasks": stale_tasks,
                "contradictions": contradictions,
                "vector_ready": vector_ready,
                "search_mode": "semantic + FTS" if vector_ready else "FTS-only",
            }
        finally:
            if owned_conn:
                conn.close()

    @staticmethod
    def revision(conn) -> int:
        row = conn.execute("SELECT revision FROM dashboard_state WHERE id = 1").fetchone()
        return int(row[0]) if row else 0

    def scope_list(self) -> dict[str, Any]:
        """All scopes available in the DB, with per-scope active counts."""
        conn = self.connection()
        try:
            rows = conn.execute(
                "SELECT project, COUNT(*) c FROM memories "
                "WHERE status = 'active' GROUP BY project"
            ).fetchall()
            counts: dict[str, int] = {}
            for r in rows:
                key = "global" if r["project"] is None else r["project"]
                counts[key] = counts.get(key, 0) + r["c"]
            total = sum(counts.values())
            scopes = [{"key": "all", "label": "All projects", "count": total}]
            if counts.get("global"):
                scopes.append({"key": "global", "label": "Global", "count": counts["global"]})
            for key, count in sorted(counts.items()):
                if key in ("global",):
                    continue
                scopes.append({"key": key, "label": key.split("/")[-1], "count": count})
            current = "all" if self.project == "all" else (
                "global" if self.project is None else self.project)
            return {"scopes": scopes, "current": current}
        finally:
            conn.close()

    def create_memory(self, data: dict[str, Any]) -> dict[str, Any]:
        conn = self.connection()
        try:
            existing_count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            link_to = data.get("link_to") or None
            # A fresh graph needs one bootstrap record; once it exists, the UI
            # requires an explicit relationship to keep the graph useful.
            if existing_count and not link_to:
                raise ValidationError("dashboard creation requires a meaningful link to an existing memory")
            result = store.store(
                conn,
                data.get("content", ""),
                data.get("title", ""),
                data.get("type", data.get("event_type", "memory")),
                agent_type=data.get("agent", data.get("agent_type", self.default_agent)),
                work_type=data.get("work_type") or None,
                project=self.project,
                session_id=data.get("session_id") or None,
                source_uri=data.get("source_uri") or None,
                # Human UI: an empty file field means "none", not "forgot" —
                # the required-field discipline targets agents, not this form.
                code_files=data.get("code_files") or data.get("modified_files") or "none",
                doc_files=data.get("doc_files") or "none",
                pinned=bool(data.get("pinned", False)),
                link_to=link_to,
                edge_type=data.get("edge_type", "related"),
                force=bool(data.get("force", False)),
            )
            if result["outcome"] != "stored":
                return result
            return {**result, "memory": self._summary(conn, dict(queries.resolve_ref(conn, result["node_id"]))) }
        finally:
            conn.close()

    def edit_memory(self, ref: str, data: dict[str, Any]) -> dict[str, Any]:
        allowed: dict[str, Any] = {}
        mapping = {
            "content": "content",
            "title": "title",
            "type": "event_type",
            "event_type": "event_type",
            "pinned": "pinned",
            "link_to": "link_to",
            "edge_type": "edge_type",
        }
        for input_key, output_key in mapping.items():
            if input_key in data and output_key not in allowed:
                allowed[output_key] = data[input_key]
        if not allowed:
            raise ValidationError("provide at least one editable field")
        conn = self.connection()
        try:
            memory = queries.resolve_ref(conn, ref)
            if not memory or not self._in_scope(dict(memory)):
                raise ValidationError(f"ref '{ref}' not found")
            result = ops.edit(conn, ref, **allowed)
            current = queries.resolve_ref(conn, result["node_id"])
            return {**result, "memory": self._summary(conn, dict(current))}
        finally:
            conn.close()

    def set_pin(self, ref: str, pinned: bool) -> dict[str, Any]:
        conn = self.connection()
        try:
            memory = queries.resolve_ref(conn, ref)
            if not memory or not self._in_scope(dict(memory)):
                raise ValidationError(f"ref '{ref}' not found")
            result = ops.edit(conn, ref, pinned=bool(pinned))
            current = queries.resolve_ref(conn, result["node_id"])
            return {**result, "memory": self._summary(conn, dict(current))}
        finally:
            conn.close()

    def archive_memory(self, ref: str, data: dict[str, Any]) -> dict[str, Any]:
        conn = self.connection()
        try:
            memory = queries.resolve_ref(conn, ref)
            if not memory or not self._in_scope(dict(memory)):
                raise ValidationError(f"ref '{ref}' not found")
            result = ops.edit(
                conn,
                ref,
                status="archived",
                archive_reason=data.get("reason", ""),
                link_to=data.get("link_to") or None,
                edge_type=data.get("edge_type", "related"),
            )
            current = queries.resolve_ref(conn, result["node_id"])
            return {**result, "memory": self._summary(conn, dict(current))}
        finally:
            conn.close()

    def create_edge(self, data: dict[str, Any]) -> dict[str, Any]:
        conn = self.connection()
        try:
            for ref in (data.get("source"), data.get("target")):
                memory = queries.resolve_ref(conn, ref)
                if not memory or not self._in_scope(dict(memory)):
                    raise ValidationError(f"ref '{ref}' not found")
            return ops.link_add(
                conn,
                data.get("source"),
                data.get("target"),
                data.get("type", "related"),
                data.get("reason", ""),
            )
        finally:
            conn.close()

    def remove_edge(self, edge_id: int) -> dict[str, Any]:
        conn = self.connection()
        try:
            row = conn.execute("SELECT * FROM edges WHERE id = ?", (edge_id,)).fetchone()
            if row:
                for node_id in (row["source_node_id"], row["target_node_id"]):
                    memory = queries.resolve_ref(conn, node_id)
                    if memory and not self._in_scope(dict(memory)):
                        raise ValidationError(f"edge {edge_id} is outside this dashboard scope")
            return ops.link_remove(conn, edge_id)
        finally:
            conn.close()

    @staticmethod
    def event_payload(row: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "revision": row["revision"],
            "node_id": row["node_id"],
            "changed": [],
        }
        # Edge trigger data is a tab-separated tuple of safe internal IDs.
        if row["event"].startswith("edge.") and row["data"]:
            source, target, edge_type, edge_id = row["data"].split("\t", 3)
            payload.update({
                "source": source,
                "target": target,
                "type": edge_type,
                "edge_id": int(edge_id),
            })
        return payload


class DashboardHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def _handler_class(app: DashboardApp):
    class DashboardHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "TrailmemDashboard/1"

        def log_message(self, format: str, *args) -> None:
            # Normal browser asset/API requests should not clutter the CLI.
            return

        LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}

        def _reject_foreign_client(self) -> bool:
            """Block DNS-rebinding and cross-site requests at the loopback boundary."""
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].lower()
            origin = self.headers.get("Origin")
            origin_host = urlparse(origin).hostname if origin else None
            if host in self.LOOPBACK_HOSTS and (origin is None or origin_host in self.LOOPBACK_HOSTS):
                return False
            self._json({"error": "request must come from this machine"}, HTTPStatus.FORBIDDEN)
            return True

        def end_headers(self) -> None:
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
                "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
                "base-uri 'none'; frame-ancestors 'none'",
            )
            super().end_headers()

        def _json(self, data: Any, status: int = HTTPStatus.OK) -> None:
            encoded = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def _html(self) -> None:
            encoded = DASHBOARD_HTML.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def _error(self, message: str, status: int = HTTPStatus.BAD_REQUEST) -> None:
            self._json({"error": message}, status)

        def _payload(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 1_000_000:
                raise ValidationError("request body is too large")
            raw = self.rfile.read(length) if length else b"{}"
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValidationError("request body must be valid JSON") from exc
            if not isinstance(value, dict):
                raise ValidationError("request body must be a JSON object")
            return value

        def _parts(self) -> tuple[str, list[str], dict[str, list[str]]]:
            parsed = urlparse(self.path)
            return parsed.path, [unquote(part) for part in parsed.path.split("/") if part], parse_qs(parsed.query)

        def do_GET(self) -> None:
            if self._reject_foreign_client():
                return
            path, parts, query = self._parts()
            try:
                if path == "/":
                    self._html()
                elif path == "/api/snapshot":
                    self._json(app.snapshot())
                elif path == "/api/scope-list":
                    self._json(app.scope_list())
                elif path == "/api/stats":
                    self._json(app.stats())
                elif path == "/api/search":
                    self._json(app.search(query.get("q", [""])[0]))
                elif path == "/events":
                    self._events(query)
                elif len(parts) == 3 and parts[:2] == ["api", "memories"] and parts[2]:
                    detail = app.detail(parts[2])
                    if detail is None:
                        self._error("memory not found", HTTPStatus.NOT_FOUND)
                    else:
                        self._json(detail)
                elif len(parts) == 4 and parts[:2] == ["api", "memories"] and parts[3] == "summary":
                    summary = app.memory_summary(parts[2])
                    if summary is None:
                        self._error("memory not found", HTTPStatus.NOT_FOUND)
                    else:
                        self._json(summary)
                else:
                    self._error("not found", HTTPStatus.NOT_FOUND)
            except ValidationError as exc:
                self._error(str(exc))
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as exc:  # pragma: no cover - defensive transport boundary
                self._error(str(exc), HTTPStatus.INTERNAL_SERVER_ERROR)

        def do_POST(self) -> None:
            if self._reject_foreign_client():
                return
            _, parts, _ = self._parts()
            try:
                data = self._payload()
                if parts == ["api", "scope"]:
                    scope = data.get("scope")
                    app.set_scope(None if scope in ("global", "", None) else scope)
                    self._json(app.snapshot())
                elif parts == ["api", "memories"]:
                    result = app.create_memory(data)
                    if result["outcome"] != "stored":
                        self._json(result, HTTPStatus.CONFLICT)
                    else:
                        self._json(result, HTTPStatus.CREATED)
                elif len(parts) == 4 and parts[:2] == ["api", "memories"] and parts[3] == "archive":
                    self._json(app.archive_memory(parts[2], data))
                elif len(parts) == 4 and parts[:2] == ["api", "memories"] and parts[3] == "pin":
                    self._json(app.set_pin(parts[2], bool(data.get("pinned", True))))
                elif parts == ["api", "edges"]:
                    self._json(app.create_edge(data), HTTPStatus.CREATED)
                else:
                    self._error("not found", HTTPStatus.NOT_FOUND)
            except ValidationError as exc:
                self._error(str(exc))
            except Exception as exc:  # pragma: no cover - defensive transport boundary
                self._error(str(exc), HTTPStatus.INTERNAL_SERVER_ERROR)

        def do_PUT(self) -> None:
            if self._reject_foreign_client():
                return
            _, parts, _ = self._parts()
            try:
                if len(parts) == 3 and parts[:2] == ["api", "memories"]:
                    self._json(app.edit_memory(parts[2], self._payload()))
                else:
                    self._error("not found", HTTPStatus.NOT_FOUND)
            except ValidationError as exc:
                self._error(str(exc))
            except Exception as exc:  # pragma: no cover - defensive transport boundary
                self._error(str(exc), HTTPStatus.INTERNAL_SERVER_ERROR)

        def do_DELETE(self) -> None:
            if self._reject_foreign_client():
                return
            _, parts, _ = self._parts()
            try:
                if len(parts) == 3 and parts[:2] == ["api", "edges"]:
                    self._json(app.remove_edge(int(parts[2])))
                else:
                    self._error("not found", HTTPStatus.NOT_FOUND)
            except ValueError:
                self._error("edge id must be an integer")
            except ValidationError as exc:
                self._error(str(exc))
            except Exception as exc:  # pragma: no cover - defensive transport boundary
                self._error(str(exc), HTTPStatus.INTERNAL_SERVER_ERROR)

        def _events(self, query: dict[str, list[str]]) -> None:
            raw_since = query.get("since_revision", [None])[0] or self.headers.get("Last-Event-ID")
            try:
                since = int(raw_since) if raw_since not in (None, "") else None
            except ValueError:
                self._error("since_revision must be an integer")
                return

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            conn = app.connection()
            try:
                current = app.revision(conn)
                if since is None:
                    since = current
                oldest = conn.execute("SELECT MIN(revision) FROM dashboard_events").fetchone()[0]
                if (oldest is None and since < current) or (oldest is not None and since < int(oldest) - 1):
                    self._write_event("reset", current, {"revision": current})
                    since = current
                last_keepalive = time.monotonic()
                while True:
                    rows = conn.execute(
                        "SELECT revision, event, node_id, data FROM dashboard_events "
                        "WHERE revision > ? ORDER BY revision",
                        (since,),
                    ).fetchall()
                    for row in rows:
                        event = dict(row)
                        payload = app.event_payload(event)
                        self._write_event(event["event"], event["revision"], payload)
                        # Statistics are derived from the changed graph. Send a
                        # second, id-less patch signal so reconnect cursors stay
                        # tied to the authoritative mutation revision.
                        self._write_event(
                            "stats.updated",
                            event["revision"],
                            {"revision": event["revision"]},
                            include_id=False,
                        )
                        since = event["revision"]
                    now = time.monotonic()
                    if now - last_keepalive >= 15:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        last_keepalive = now
                    time.sleep(0.35)
            except (BrokenPipeError, ConnectionResetError):
                return
            finally:
                conn.close()

        def _write_event(
            self,
            event: str,
            revision: int,
            payload: dict[str, Any],
            *,
            include_id: bool = True,
        ) -> None:
            encoded = json.dumps(payload, ensure_ascii=False, default=str)
            prefix = f"id: {revision}\n" if include_id else ""
            self.wfile.write(f"{prefix}event: {event}\ndata: {encoded}\n\n".encode("utf-8"))
            self.wfile.flush()

    return DashboardHandler


def serve(*, port: int = 3800, project: str | None = "all", default_agent: str = "user") -> int:
    """Run the loopback-only dashboard until the user presses Ctrl-C.

    Default scope is "all" (global + every project). Use --project to start
    in a narrower scope; the in-UI switcher can change it at runtime anyway.
    """
    app = DashboardApp(project, default_agent)
    # Initializing before binding gives a clear error before the user sees a URL.
    conn = app.connection()
    conn.close()
    try:
        server = DashboardHTTPServer(("127.0.0.1", port), _handler_class(app))
    except OSError as exc:
        print(f"Cannot bind 127.0.0.1:{port}: {exc.strerror or exc}. "
              "Is another dashboard running? Try --port.", file=sys.stderr)
        return 1
    print(f"Trailmem dashboard: http://127.0.0.1:{port}")
    print("Scope: " + app.scope_label() + "  •  Ctrl-C to stop")
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
    finally:
        server.server_close()
    return 0


DASHBOARD_HTML = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trailmem dashboard</title>
<style>
:root{color-scheme:light dark;--bg:#0e1014;--panel:#171a21;--panel-2:#1d212b;--ink:#e7eaf0;--muted:#8a93a6;--line:#2a2f3a;--line-soft:#22262f;--focus:#7c8cff;--accent:#7c8cff;--accent-soft:#9aa6ff;--danger:#ff6b7d;--good:#3ecf8e;--warn:#e5a84b;--shadow:0 8px 24px rgba(0,0,0,.45);--radius:14px;--font:'Segoe UI',system-ui,-apple-system,Roboto,Helvetica,Arial,sans-serif;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
@media(prefers-color-scheme:light){:root{--bg:#f4f6fb;--panel:#ffffff;--panel-2:#f7f9fd;--ink:#1b1f29;--muted:#5d6680;--line:#e2e6ef;--line-soft:#eceff5;--shadow:0 8px 24px rgba(20,30,60,.10)}}
*{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 var(--font)}button,input,select,textarea{font:inherit}button{color:var(--ink);background:var(--panel-2);border:1px solid var(--line);border-radius:9px;padding:.45rem .7rem;cursor:pointer;transition:border-color .15s,background .15s}button:hover{border-color:var(--accent)}button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{outline:3px solid color-mix(in srgb,var(--focus) 45%,transparent);outline-offset:2px}button.primary{background:var(--accent);color:#fff;border-color:var(--accent)}button.primary:hover{background:var(--accent-soft)}button.quiet{border-color:transparent;background:transparent}.topbar{height:58px;padding:0 18px;display:flex;align-items:center;gap:14px;border-bottom:1px solid var(--line);background:color-mix(in srgb,var(--panel) 92%,transparent);backdrop-filter:blur(10px);position:sticky;top:0;z-index:20}.brand{font-weight:800;font-size:16px;letter-spacing:-.02em;white-space:nowrap;display:flex;align-items:center;gap:8px}.brand .mark{width:11px;height:11px;border-radius:3px;background:linear-gradient(135deg,var(--accent),var(--good));box-shadow:0 0 10px color-mix(in srgb,var(--accent) 60%,transparent)}.scope{color:var(--muted);font-size:12px;max-width:18vw;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.search{min-width:160px;max-width:420px;flex:1;padding:.5rem .75rem;border:1px solid var(--line);border-radius:9px;background:var(--bg);color:var(--ink)}.status{font-size:12px;color:var(--muted);white-space:nowrap;display:flex;align-items:center;gap:6px}.status::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--muted)}.status.online{color:var(--good)}.status.online::before{background:var(--good);box-shadow:0 0 8px var(--good)}.counters{display:flex;gap:6px}.counter{padding:.3rem .55rem;font-size:12px;white-space:nowrap;border:1px solid var(--line);border-radius:999px;background:var(--panel-2);cursor:pointer;color:var(--muted)}.counter:hover{border-color:var(--accent);color:var(--ink)}.workspace{display:grid;grid-template-columns:340px 1fr 400px;gap:14px;padding:14px;height:calc(100vh - 58px);overflow:hidden}.panel{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow);min-width:0;display:flex;flex-direction:column;overflow:hidden}.panel-head{display:flex;align-items:center;justify-content:space-between;padding:11px 13px;border-bottom:1px solid var(--line-soft);gap:8px}.panel-head h2{font-size:12px;margin:0;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);font-weight:700}.legend{font-size:11px;color:var(--muted);display:flex;gap:8px;align-items:center}.dot{width:8px;height:8px;border-radius:50%;display:inline-block;background:var(--accent)}
.graph-wrap{position:relative;overflow:visible;height:100%;isolation:isolate;--graph-bg:#0b0d12;--graph-surface:#10131a;--graph-grid:#ffffff0c;--graph-line:#7e879c;--graph-text:#dce2f2;--graph-muted:#7b8499;--graph-accent:#9aa6ff;--node-scale:1}.graph-wrap .panel-head{position:relative;z-index:6}.graph-title{display:flex;align-items:baseline;gap:8px;min-width:0}.graph-subtitle{font-size:11px;color:var(--graph-muted);font-weight:500;white-space:nowrap}.graph-actions{display:flex;align-items:center;gap:5px}.graph-actions>button,.graph-settings>summary{height:30px;min-width:30px;padding:4px 9px;border:1px solid var(--line);border-radius:8px;background:var(--panel-2);display:inline-flex;align-items:center;justify-content:center;gap:5px;color:var(--graph-muted);font-size:12px;list-style:none;cursor:pointer}.graph-actions>button:hover,.graph-settings>summary:hover,.graph-settings[open]>summary{border-color:var(--graph-accent);color:var(--graph-text);background:color-mix(in srgb,var(--graph-accent) 12%,var(--panel-2))}.graph-actions>button.icon{font-size:17px;line-height:1;padding:2px 8px}.graph-settings{position:relative}.graph-settings>summary::-webkit-details-marker{display:none}.graph-settings-panel{position:absolute;right:0;top:36px;width:278px;padding:12px;background:var(--panel);border:1px solid var(--line);border-radius:10px;box-shadow:var(--shadow);display:grid;gap:11px;z-index:12}.graph-setting{display:grid;grid-template-columns:1fr auto;align-items:center;gap:10px;color:var(--ink);font-size:12px;font-weight:600}.graph-setting select{min-width:110px}.graph-setting input[type=range]{width:116px;padding:0;border:0}.graph-setting input[type=checkbox]{width:16px;height:16px;margin:0;padding:0}.graph-setting output{min-width:30px;color:var(--muted);font-variant-numeric:tabular-nums;text-align:right}.graph-setting-range{grid-template-columns:1fr 116px 34px}.graph-setting-note{margin:0;color:var(--muted);font-size:11px;font-weight:400}.graph-stage{position:relative;height:calc(100% - 53px);min-height:260px;overflow:hidden;background:radial-gradient(circle at 50% 42%,#181c26 0,#0e1118 45%,var(--graph-bg) 80%)}.graph-stage::before{content:"";position:absolute;inset:0;pointer-events:none;background-image:radial-gradient(circle,var(--graph-grid) 1px,transparent 1px);background-size:26px 26px;opacity:.7;mask-image:radial-gradient(circle at center,#000 10%,transparent 85%)}#graph{position:relative;width:100%;height:100%;display:block;touch-action:none;cursor:grab;user-select:none}#graph.panning{cursor:grabbing}.edge{color:var(--edge-color,var(--graph-line))}.edge-line{fill:none;stroke:currentColor;stroke-opacity:.55;stroke-width:1.6;vector-effect:non-scaling-stroke;transition:stroke-opacity .16s ease,stroke-width .16s ease}.edge-hit{fill:none;stroke:transparent;stroke-width:14;pointer-events:stroke}.edge.contradicts{color:#ff6b7d}.edge.contradicts .edge-line{stroke-dasharray:5 4}.edge.supersedes .edge-line{stroke-dasharray:8 3}.edge.derived_from .edge-line{stroke-dasharray:2 4}.edge.evolves .edge-line{stroke-dasharray:11 3 2 3}.edge.is-dim .edge-line{stroke-opacity:.05}.edge.is-focus .edge-line{stroke-opacity:.95;stroke-width:2.2}.node{cursor:pointer;outline:none}.node .node-halo{fill:transparent;stroke:transparent;stroke-width:9;transition:fill .16s ease,stroke .16s ease}.node .node-core{fill:var(--comm,#9aa3b8);stroke:var(--comm,#9aa3b8);stroke-width:1.5;filter:drop-shadow(0 2px 5px #0009);transition:opacity .16s ease,fill .16s ease,stroke .16s ease,stroke-width .16s ease}.node.selected .node-halo,.node.hovered .node-halo{fill:#9aa6ff26;stroke:#b8adff90}.node.hovered .node-core{fill:#fff}.node.selected .node-core{fill:#fff;stroke-width:3}.node.neighbor .node-core{stroke-width:3}.node.archived .node-core{opacity:.45}.node.dim{opacity:.14}.node text{fill:var(--graph-text);font-size:12.5px;font-weight:600;pointer-events:none;paint-order:stroke;stroke:#0b0d12;stroke-width:4px;stroke-linejoin:round;opacity:0;transition:opacity .14s ease}.node .pin-ring{fill:none;stroke:var(--warn);stroke-width:2;opacity:.95}.node .pin-mark{fill:var(--warn);stroke:none;font-size:11px;font-weight:700;opacity:.95}.node.pinned .node-core{stroke:var(--warn);stroke-width:2.5}.node.pinned.selected .node-core{stroke:#fff}.satellite{opacity:0;transition:opacity .18s ease;pointer-events:none}.node.selected .satellite{opacity:1;pointer-events:auto}.sat-dot{stroke:#0b0d12;stroke-width:1.5}.satellite.code .sat-dot{fill:var(--good)}.satellite.doc .sat-dot{fill:#71b7ff}.sat-label{fill:var(--graph-text);font-size:10px;font-weight:600;paint-order:stroke;stroke:#0b0d12;stroke-width:3px;stroke-linejoin:round;pointer-events:none;font-family:var(--mono)}.sat-more{fill:var(--graph-muted);font-size:10px;text-anchor:middle;font-weight:600}.node.selected .node-label,.node.hovered .node-label,.node.neighbor .node-label,.graph-stage.labels-expanded .node-label{opacity:1}.graph-stage[data-label-mode=always] .node-label{opacity:1}.graph-stage[data-label-mode=off] .node-label{opacity:0!important}.graph-stage[data-label-mode=hover] .node-label{opacity:0}.graph-stage[data-label-mode=hover] .node.selected .node-label,.graph-stage[data-label-mode=hover] .node.hovered .node-label{opacity:1}.node:focus-visible .node-halo{fill:#9aa6ff2e;stroke:var(--graph-accent);stroke-width:5}.graph-hud{position:absolute;left:12px;top:12px;display:flex;gap:6px;align-items:center;pointer-events:none}.graph-pill{padding:4px 9px;border:1px solid #ffffff14;border-radius:999px;background:#0d0f17c0;color:var(--graph-muted);font-size:11px;backdrop-filter:blur(8px)}.graph-legend{position:absolute;left:12px;bottom:11px;display:flex;gap:10px;align-items:center;color:var(--graph-muted);font-size:10px;pointer-events:none}.graph-legend span{display:flex;align-items:center;gap:4px}.graph-legend i{width:7px;height:7px;border-radius:50%;background:#9aa6ff;box-shadow:0 0 0 2px #9aa6ff26}.graph-legend .legend-pin{color:var(--warn)}.graph-tip{position:absolute;z-index:7;max-width:300px;padding:7px 9px;border:1px solid #ffffff1c;border-radius:7px;background:#11141ef0;color:var(--graph-text);font-size:11px;box-shadow:0 8px 24px #0008;pointer-events:none;transform:translate(10px,10px)}.graph-tip strong{display:block;color:#fff;margin-bottom:2px}.graph-tip span{color:var(--graph-muted)}.graph-empty{position:absolute;inset:0;display:grid;place-items:center;color:var(--graph-muted);font-size:13px;pointer-events:none}.graph-empty[hidden]{display:none}.list-controls{display:flex;gap:6px;align-items:center;flex-wrap:wrap}.list-controls select{border:1px solid var(--line);border-radius:7px;padding:.35rem .4rem;background:var(--bg);color:var(--ink);font-size:12px}.memory-list{flex:1;overflow-y:auto;padding:6px;min-height:0;display:flex;flex-direction:column;gap:5px}.memory-row{width:100%;display:grid;grid-template-columns:1fr auto;gap:3px 8px;text-align:left;border:1px solid transparent;border-radius:10px;padding:9px 10px;background:var(--panel-2);cursor:pointer}.memory-row:hover{border-color:var(--line);background:color-mix(in srgb,var(--accent) 7%,var(--panel-2))}.memory-row.selected{border-color:var(--accent);background:color-mix(in srgb,var(--accent) 12%,var(--panel-2))}.memory-title{font-weight:650;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display:flex;align-items:center;gap:6px}.memory-title .pin-ic{color:var(--warn);flex:none}.memory-meta,.preview{font-size:12px;color:var(--muted)}.preview{grid-column:1/-1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.badge{border:1px solid currentColor;border-radius:99px;padding:1px 6px;font-size:10px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
.inspector{border-left:none;background:transparent;min-width:0}.inspector-inner{height:100%;overflow:auto;padding:22px}.empty{color:var(--muted);padding:18px 0}.memory-header{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.memory-header h1{font-size:21px;line-height:1.18;margin:0 0 4px;word-break:break-word}.pin-btn{display:inline-flex;align-items:center;gap:5px;border:1px solid var(--line);border-radius:999px;padding:.25rem .6rem;font-size:11px;cursor:pointer;background:var(--panel-2);color:var(--muted)}.pin-btn:hover{border-color:var(--warn);color:var(--warn)}.pin-btn.on{background:color-mix(in srgb,var(--warn) 16%,transparent);border-color:var(--warn);color:var(--warn)}.actions{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end;align-items:center}.metadata{display:flex;flex-wrap:wrap;gap:6px;margin:12px 0}.meta{font-size:12px;padding:3px 7px;border-radius:6px;background:var(--panel-2);color:var(--muted);border:1px solid var(--line-soft)}.meta.pinned{color:var(--warn);border-color:color-mix(in srgb,var(--warn) 40%,transparent)}.content{white-space:pre-wrap;max-width:74ch;font-size:15px;line-height:1.7;margin:16px 0}.file-cols{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:10px 0}.file-card{border:1px solid var(--line);border-radius:10px;padding:11px 12px;background:var(--panel-2)}.file-card.filled{border-color:color-mix(in srgb,var(--good) 35%,var(--line))}.file-card .fc-head{display:flex;align-items:center;justify-content:space-between;font-size:12px;font-weight:700;margin-bottom:6px}.file-card .fc-state{font-size:10px;text-transform:uppercase;letter-spacing:.05em;padding:1px 6px;border-radius:99px;color:var(--muted)}.file-card.filled .fc-state{color:var(--good);background:color-mix(in srgb,var(--good) 14%,transparent)}.file-card.empty .fc-state{color:var(--muted)}.file-card .fc-body{font-family:var(--mono);font-size:11.5px;word-break:break-all;color:var(--ink);white-space:pre-wrap;max-height:130px;overflow:auto}.file-card.empty .fc-body{color:var(--muted);font-style:italic}.section-title{font-size:11px;text-transform:uppercase;letter-spacing:.09em;color:var(--muted);margin:20px 0 8px;display:flex;align-items:center;gap:7px}.section-title .count{background:var(--panel-2);border:1px solid var(--line);border-radius:99px;padding:0 7px;font-size:10px;color:var(--muted)}.relationship{display:flex;align-items:center;gap:8px;width:100%;text-align:left;margin:5px 0;border:1px solid var(--line-soft);border-radius:9px;padding:7px 9px;background:var(--panel-2);color:var(--ink)}.relationship:hover{border-color:var(--accent)}.relationship .direction{font-weight:800;color:var(--accent);flex:none}.relationship .rtitle{font-weight:600;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.relationship .reason{color:var(--muted);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.relationship .unlink{margin-left:auto;flex:none;color:var(--muted);font-size:11px;padding:2px 7px}.relationship .unlink:hover{color:var(--danger);border-color:var(--danger)}.archive-note{padding:9px 11px;border-left:3px solid var(--muted);background:var(--panel-2);border-radius:0 8px 8px 0;font-size:13px;margin:10px 0}.notice{position:fixed;bottom:18px;left:18px;padding:8px 12px;background:var(--ink);color:var(--panel);border-radius:8px;font-size:12px;box-shadow:var(--shadow);z-index:30}.filter-note{font-size:12px;color:var(--muted);padding:5px 10px}
dialog{max-width:min(680px,calc(100vw - 28px));width:100%;border:1px solid var(--line);border-radius:12px;background:var(--panel);color:var(--ink);box-shadow:0 16px 55px #0007}dialog::backdrop{background:#0009}dialog form{display:grid;gap:11px}dialog h2{margin:0 0 2px;font-size:18px}label{display:grid;gap:4px;font-weight:600;font-size:13px}input,select,textarea{border:1px solid var(--line);border-radius:7px;background:var(--bg);color:var(--ink);padding:.48rem .55rem}textarea{min-height:160px;resize:vertical}.form-row{display:grid;grid-template-columns:1fr 1fr;gap:10px}.dialog-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:4px}.form-error{color:var(--danger);font-size:13px;min-height:1.2em}.small{font-size:12px;color:var(--muted)}
@media(max-width:900px){.workspace{grid-template-columns:1fr;height:auto;overflow:visible}.inspector{border-left:none;border-top:1px solid var(--line)}.inspector-inner{height:auto;min-height:52vh}.explorer{grid-template-rows:460px minmax(260px,1fr)}.topbar{gap:7px;padding:8px}.scope,.legend{display:none}.counters .counter:nth-child(n+3){display:none}.file-cols{grid-template-columns:1fr}}@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}
</style>
</head>
<body>
<header class="topbar"><div class="brand">Trailmem</div><div id="scope" class="scope"></div><label class="scope-switch"><span class="scope-switch-label">Scope</span><select id="scope-switch" aria-label="Switch memory scope"></select></label><input id="search" class="search" type="search" placeholder="Search title, content, #id, or node ID" aria-label="Search memories"><span id="status" class="status">Connecting…</span><div class="counters" aria-label="Health counters"><button class="counter" data-health="orphans">0 orphans</button><button class="counter" data-health="stale">0 stale tasks</button><button class="counter" data-health="contradictions">0 contradictions</button></div><button id="create" class="primary">New memory</button></header>
<main class="workspace">
      <section class="panel"><div class="panel-head"><h2>Memories <span id="result-count" class="small"></span></h2><div class="list-controls"><select id="type-filter" aria-label="Filter memories by type"><option value="">All types</option><option>decision</option><option>lesson</option><option>error_pattern</option><option>task</option><option>memory</option><option>user_preference</option><option>constraint</option><option>session_summary</option></select><select id="status-filter" aria-label="Filter memories by status"><option value="">All statuses</option><option value="active">Active</option><option value="archived">Archived</option><option value="superseded">Superseded</option><option value="orphans">Orphans</option></select><select id="agent-filter" aria-label="Filter memories by agent"><option value="">All agents</option><option>user</option><option>kiro</option><option>claude</option><option>codex</option><option>opencode</option><option>kilo</option><option>antigravity</option></select><select id="project-filter" aria-label="Filter memories by project"><option value="">All projects</option></select><select id="pinned-filter" aria-label="Filter pinned memories"><option value="">Pinned + unpinned</option><option value="yes">Pinned only</option><option value="no">Unpinned</option></select></div></div><div id="filter-note" class="filter-note" hidden></div><div id="memory-list" class="memory-list" aria-label="Memory list"></div></section>
      <section class="panel graph-wrap"><div class="panel-head"><div class="graph-title"><h2>Memory graph</h2><span id="graph-summary" class="graph-subtitle">0 memories · 0 links</span></div><div class="graph-actions"><button id="zoom-out" class="icon" type="button" title="Zoom out" aria-label="Zoom out">−</button><button id="zoom-in" class="icon" type="button" title="Zoom in" aria-label="Zoom in">+</button><button id="fit-graph" type="button" title="Fit all memories in view">Fit</button><button id="relayout" type="button" title="Release pinned nodes and re-layout graph">Re-layout</button><details id="graph-settings" class="graph-settings"><summary title="Graph display settings" aria-label="Graph display settings">Settings</summary><div class="graph-settings-panel"><label class="graph-setting">Labels<select id="label-mode"><option value="auto">Auto</option><option value="always">Always</option><option value="hover">On hover</option><option value="off">Off</option></select></label><label class="graph-setting"><span>Direction arrows</span><input id="show-arrows" type="checkbox" checked></label><label class="graph-setting"><span>Dim unrelated</span><input id="dim-neighbors" type="checkbox" checked></label><label class="graph-setting graph-setting-range"><span>Node size</span><input id="node-scale" type="range" min="75" max="150" value="100"><output id="node-scale-value">100%</output></label><label class="graph-setting graph-setting-range"><span>Link length</span><input id="link-distance" type="range" min="80" max="420" value="150"><output id="link-distance-value">150</output></label><label class="graph-setting graph-setting-range"><span>Repel force</span><input id="repel-force" type="range" min="1" max="20" value="9"><output id="repel-force-value">9</output></label><p class="graph-setting-note">Drag a node to freeze its position. Use the pin ◉ in a memory's inspector to keep it in view.</p></div></details></div></div><div id="graph-stage" class="graph-stage" data-label-mode="auto"><svg id="graph" viewBox="-320 -230 640 460" role="group" aria-label="Interactive memory relationship graph"><defs><marker id="arrow" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="4.6" markerHeight="4.6" orient="auto-start-reverse"><path d="M 0 1 L 9.5 5 L 0 9 z" fill="context-stroke" fill-opacity=".9"></path></marker></defs><g id="viewport"><g id="edge-layer"></g><g id="node-layer"></g></g></svg><div class="graph-hud"><span id="graph-zoom" class="graph-pill">100%</span><span id="graph-selection" class="graph-pill">Drag to pan · scroll to zoom</span></div><div class="graph-legend" aria-hidden="true"><span><i></i> cluster</span><span>— link</span><span class="legend-pin">◉ pinned</span></div><div id="edge-tip" class="graph-tip" hidden role="status"></div><div id="graph-empty" class="graph-empty" hidden>No memories match the current filters.</div></div></section>
      <aside class="panel inspector"><div id="inspector" class="inspector-inner"><div class="empty">Select a memory in the graph or list to read it in full.</div></div></aside></main>
<dialog id="memory-dialog"><form id="memory-form" method="dialog"><h2>New memory</h2><p class="small">New records must link to an existing memory after the first bootstrap record.</p><label>Title <input name="title" required minlength="3" maxlength="60"></label><div class="form-row"><label>Type <select name="type"><option>memory</option><option>decision</option><option>lesson</option><option>error_pattern</option><option>task</option><option>user_preference</option><option>constraint</option><option>session_summary</option></select></label><label>Agent <select name="agent"><option>user</option><option>kiro</option><option>claude</option><option>codex</option><option>opencode</option><option>kilo</option><option>antigravity</option></select></label></div><label>Content <textarea name="content" required minlength="50" placeholder="Capture durable context, rationale, or a verified outcome."></textarea></label><div class="form-row"><label>Link to (ID or node ID) <input name="link_to" placeholder="#12 or mem-a1b2c3d4"></label><label>Relationship <select name="edge_type"><option>related</option><option>derived_from</option><option>supersedes</option><option>contradicts</option><option>evolves</option></select></label></div><div class="form-error" aria-live="polite"></div><div class="dialog-actions"><button type="button" data-close>Cancel</button><button class="primary" type="submit">Store memory</button></div></form></dialog>
<dialog id="edit-dialog"><form id="edit-form" method="dialog"><h2>Edit memory</h2><label>Title <input name="title" required minlength="3" maxlength="60"></label><label>Content <textarea name="content" required minlength="50"></textarea></label><div class="form-error" aria-live="polite"></div><div class="dialog-actions"><button type="button" data-close>Cancel</button><button class="primary" type="submit">Save changes</button></div></form></dialog>
<dialog id="archive-dialog"><form id="archive-form" method="dialog"><h2>Archive memory</h2><p class="small">Archiving preserves the trail. A reason of at least 20 characters and a relationship are required.</p><label>Reason <textarea name="reason" required minlength="20"></textarea></label><label>Link to, if this memory has no relationships <input name="link_to" placeholder="#12 or mem-a1b2c3d4"></label><div class="form-error" aria-live="polite"></div><div class="dialog-actions"><button type="button" data-close>Cancel</button><button class="primary" type="submit">Archive</button></div></form></dialog>
<dialog id="link-dialog"><form id="link-form" method="dialog"><h2>Link memories</h2><label>Source <input name="source" required></label><label>Target <input name="target" required></label><div class="form-row"><label>Relationship <select name="type"><option>related</option><option>derived_from</option><option>supersedes</option><option>contradicts</option><option>evolves</option></select></label><label>Reason (optional) <input name="reason"></label></div><div class="form-error" aria-live="polite"></div><div class="dialog-actions"><button type="button" data-close>Cancel</button><button class="primary" type="submit">Create link</button></div></form></dialog>
<div id="notice" class="notice" hidden aria-live="polite"></div>
<script>
(() => {
  const $ = s => document.querySelector(s), svg = (tag, attrs={}) => { const el=document.createElementNS('http://www.w3.org/2000/svg',tag); for(const [k,v] of Object.entries(attrs)) el.setAttribute(k,v); return el; };
  const state = {nodes:[], edges:[], revision:0, selected:null, detail:null, positions:new Map(), clusterCenters:new Map(), fixed:new Set(), view:{x:-320,y:-230,w:640,h:460}, eventSource:null, conflict:false, searchNodeIds:null, hovered:null, layoutReady:false, animation:null, pan:null, nodeDrag:null, suppressClick:false, viewTouched:false, communities:new Map(), clusterLegend:[], maxDegree:1, hubThreshold:99, settings:{labelMode:'auto',showArrows:true,dimNeighbors:true,nodeScale:1,linkDistance:150,charge:9}};
  const graph=$('#graph'), graphStage=$('#graph-stage'), viewport=$('#viewport'), edgeLayer=$('#edge-layer'), nodeLayer=$('#node-layer'), memoryList=$('#memory-list');
  const api = async (path, options={}) => { const r=await fetch(path,{headers:{'Content-Type':'application/json',...(options.headers||{})},...options}); const body=await r.json().catch(()=>({error:`HTTP ${r.status}`})); if(!r.ok){const e=new Error(body.error||body.message||`HTTP ${r.status}`);e.body=body;throw e;} return body; };
  const escapeText = value => String(value ?? '');
  function notice(text){const n=$('#notice');n.textContent=text;n.hidden=false;clearTimeout(notice.timer);notice.timer=setTimeout(()=>n.hidden=true,3200)}
  function formData(form){return Object.fromEntries(new FormData(form).entries())}
  function closeDialog(button){button.closest('dialog').close()}
  document.querySelectorAll('[data-close]').forEach(b=>b.addEventListener('click',()=>closeDialog(b)));
  function nodeRadius(node){return (node?.pinned?22:18)*state.settings.nodeScale}
  function nodeById(id){return state.nodes.find(n=>n.node_id===id)}
  // Graphify-style organisation: every connected cluster gets its own circular
  // territory (largest in the middle, smaller ones packed around it, orphans on
  // the rim), so separate clusters never tangle into one central hairball.
  function componentGroups(){const adj=adjacency(),seen=new Set(),groups=[];for(const node of state.nodes){if(seen.has(node.node_id))continue;const queue=[node.node_id],members=[];seen.add(node.node_id);while(queue.length){const cur=queue.pop();members.push(cur);for(const next of adj.get(cur)||[])if(!seen.has(next)){seen.add(next);queue.push(next)}}groups.push(members)}return groups.sort((a,b)=>b.length-a.length)}
  function packComponents(groups){const placed=[],centers=new Map(),spacing=Math.max(80,state.settings.linkDistance);for(const members of groups){const radius=spacing*(0.6+Math.sqrt(members.length)*0.8);let x=0,y=0;if(placed.length){for(let step=1;step<2400;step++){const angle=step*2.399963229728653,dist=step*spacing*0.05,cx=Math.cos(angle)*dist,cy=Math.sin(angle)*dist;if(placed.every(c=>Math.hypot(cx-c.x,cy-c.y)>=c.r+radius+spacing*0.35)){x=cx;y=cy;break}}}placed.push({x,y,r:radius});for(const id of members)centers.set(id,{x,y,r:radius})}return centers}
  function ensurePositions(reset=false){if(reset){state.positions.clear();state.fixed.clear()}state.clusterCenters=packComponents(componentGroups());const known=new Map(state.positions);state.nodes.forEach((node,i)=>{if(state.positions.has(node.node_id))return;const neighbors=state.edges.flatMap(edge=>edge.source===node.node_id?[edge.target]:edge.target===node.node_id?[edge.source]:[]).map(id=>known.get(id)).filter(Boolean);if(neighbors.length){const x=neighbors.reduce((sum,p)=>sum+p.x,0)/neighbors.length,y=neighbors.reduce((sum,p)=>sum+p.y,0)/neighbors.length,angle=(i+1)*2.399963229728653;state.positions.set(node.node_id,{x:x+Math.cos(angle)*54,y:y+Math.sin(angle)*54})}else{const home=state.clusterCenters.get(node.node_id)||{x:0,y:0,r:120},angle=i*2.399963229728653,radius=home.r*0.75*Math.sqrt((i%97+1)/97);state.positions.set(node.node_id,{x:home.x+Math.cos(angle)*radius,y:home.y+Math.sin(angle)*radius})}})}
  function layout(reset=false){ensurePositions(reset)}
  function updateView(){graph.setAttribute('viewBox',`${state.view.x} ${state.view.y} ${state.view.w} ${state.view.h}`);const zoom=Math.round(1000/state.view.w*100);$('#graph-zoom').textContent=`${zoom}%`;graphStage.classList.toggle('labels-expanded',state.settings.labelMode==='auto'&&state.view.w<760)}
  function clientToGraph(clientX,clientY){const r=graph.getBoundingClientRect();return{x:state.view.x+(clientX-r.left)/Math.max(r.width,1)*state.view.w,y:state.view.y+(clientY-r.top)/Math.max(r.height,1)*state.view.h}}
  function visibleNodes(){const search=$('#search').value.trim().toLowerCase(), type=$('#type-filter').value, status=$('#status-filter').value, agent=$('#agent-filter').value, project=$('#project-filter').value, pinned=$('#pinned-filter').value;return state.nodes.filter(n=>{const hay=`${n.id} ${n.node_id} ${n.title} ${n.preview}`.toLowerCase(); if(search&&state.searchNodeIds&&!state.searchNodeIds.has(n.node_id))return false;if(search&&!state.searchNodeIds&&!hay.includes(search))return false;if(type&&n.type!==type)return false;if(status==='orphans'&&n.edge_count!==0)return false;if(status&&status!=='orphans'&&n.status!==status)return false;if(agent&&n.agent!==agent)return false;if(project&&(n.project||'')!==project)return false;if(pinned==='yes'&&!n.pinned)return false;if(pinned==='no'&&n.pinned)return false;return true})}
  function visibleNodeIds(){return new Set(visibleNodes().map(n=>n.node_id))}
  function adjacency(){const map=new Map(state.nodes.map(n=>[n.node_id,new Set()]));for(const edge of state.edges){map.get(edge.source)?.add(edge.target);map.get(edge.target)?.add(edge.source)}return map}
  // Graphify-style communities: label propagation (seeded from the previous
  // run so colours stay stable across live patches), Tableau-10 colours
  // assigned largest-cluster-first, hub = highest-degree member — the same
  // colour organisation graphify's viewer renders.
  const clusterPalette=['#4E79A7','#F28E2B','#E15759','#76B7B2','#59A14F','#EDC948','#B07AA1','#FF9DA7','#9C755F','#BAB0AC'];
  function computeCommunities(){const adj=adjacency(),label=new Map(state.nodes.map(n=>[n.node_id,state.communities.get(n.node_id)?.cid??n.node_id]));for(let pass=0;pass<6;pass++){let changed=false;for(const node of state.nodes){const id=node.node_id,counts=new Map();for(const nb of adj.get(id)||[]){const l=label.get(nb);if(l!==undefined)counts.set(l,(counts.get(l)||0)+1)}if(!counts.size)continue;let best=label.get(id),bestCount=counts.get(best)||0;for(const [l,c] of counts)if(c>bestCount||(c===bestCount&&String(l)<String(best))){best=l;bestCount=c}if(best!==label.get(id)){label.set(id,best);changed=true}}if(!changed)break}const groups=new Map();for(const [id,l] of label){if(!groups.has(l))groups.set(l,[]);groups.get(l).push(id)}const sorted=[...groups.entries()].sort((a,b)=>b[1].length-a[1].length||String(a[0]).localeCompare(String(b[0])));state.communities=new Map();state.clusterLegend=[];sorted.forEach(([cid,members],index)=>{const color=clusterPalette[index%clusterPalette.length];let hub=members[0],hubDegree=-1;for(const id of members){const d=Number(nodeById(id)?.edge_count)||0;if(d>hubDegree){hubDegree=d;hub=id}}for(const id of members)state.communities.set(id,{cid,color});state.clusterLegend.push({color,hub,size:members.length})});state.maxDegree=Math.max(1,...state.nodes.map(n=>Number(n.edge_count)||0));state.hubThreshold=Math.max(2,Math.ceil(state.maxDegree*.5))}
  function communityColor(id){return state.communities.get(id)?.color||'#9aa3b8'}
  // Full end-to-end connected set via BFS — every memory reachable from `id`
  // through any chain of relationships, not just direct neighbors.
  function connectedSet(id){const adj=adjacency(),seen=new Set(),queue=[id];while(queue.length){const cur=queue.shift();if(seen.has(cur))continue;seen.add(cur);for(const next of adj.get(cur)||[])if(!seen.has(next))queue.push(next)}return seen}
  function edgeCoordinates(edge){const a=state.positions.get(edge.source),b=state.positions.get(edge.target);if(!a||!b)return null;const dx=b.x-a.x,dy=b.y-a.y,d=Math.hypot(dx,dy)||1,sourcePad=nodeRadius(nodeById(edge.source))+2,targetPad=nodeRadius(nodeById(edge.target))+(state.settings.showArrows?7:3);return{x1:a.x+dx/d*sourcePad,y1:a.y+dy/d*sourcePad,x2:b.x-dx/d*targetPad,y2:b.y-dy/d*targetPad}}
  // Gentle quadratic curve, like graphify's smooth continuous edges.
  function edgePathD(c){const mx=(c.x1+c.x2)/2,my=(c.y1+c.y2)/2,k=.1;return`M ${c.x1} ${c.y1} Q ${mx-(c.y2-c.y1)*k} ${my+(c.x2-c.x1)*k} ${c.x2} ${c.y2}`}
  function markerFor(){return'url(#arrow)'}
  function hideEdgeTip(){$('#edge-tip').hidden=true}
  function showEdgeTip(edge,event){const tip=$('#edge-tip'),source=nodeById(edge.source),target=nodeById(edge.target);tip.replaceChildren();const strong=document.createElement('strong');strong.textContent=`${source?.title||edge.source} → ${target?.title||edge.target}`;const meta=document.createElement('span');meta.textContent=edge.reason?`${edge.type} · ${edge.reason}`:edge.type.replaceAll('_',' ');tip.append(strong,meta);tip.hidden=false;moveEdgeTip(event)}
  function moveEdgeTip(event){const tip=$('#edge-tip'),r=graphStage.getBoundingClientRect();tip.style.left=`${Math.max(4,Math.min(r.width-310,event.clientX-r.left))}px`;tip.style.top=`${Math.max(4,Math.min(r.height-70,event.clientY-r.top))}px`}
  function makeEdgeElement(edge,visible=visibleNodeIds()){if(!visible.has(edge.source)||!visible.has(edge.target))return null;const coordinates=edgeCoordinates(edge);if(!coordinates)return null;const d=edgePathD(coordinates),group=svg('g',{class:`edge ${edge.type}`,'data-edge-id':edge.id,'data-source':edge.source,'data-target':edge.target,role:'img','aria-label':`${edge.type.replaceAll('_',' ')} relationship`}),line=svg('path',{d,class:'edge-line'}),hit=svg('path',{d,class:'edge-hit'});group.style.setProperty('--edge-color',communityColor(edge.source));if(state.settings.showArrows)line.setAttribute('marker-end',markerFor());group.append(line,hit);group.addEventListener('pointerenter',e=>{group.classList.add('is-focus');showEdgeTip(edge,e)});group.addEventListener('pointermove',moveEdgeTip);group.addEventListener('pointerleave',()=>{hideEdgeTip();applyGraphFocus()});return group}
  function beginNodeDrag(event,id){if(event.button!==0)return;event.preventDefault();event.stopPropagation();event.currentTarget.focus({preventScroll:true});const p=clientToGraph(event.clientX,event.clientY),wasFixed=state.fixed.has(id);state.nodeDrag={id,pointerId:event.pointerId,startX:p.x,startY:p.y,moved:false,wasFixed};graph.setPointerCapture(event.pointerId);state.fixed.add(id);focusGraphNode(id,true)}
  function makeNodeElement(node){const p=state.positions.get(node.node_id),radius=nodeRadius(node),g=svg('g',{class:`node ${node.status!=='active'?'archived':''} ${node.pinned?'pinned':''} ${state.selected===node.node_id?'selected':''}`,transform:`translate(${p.x} ${p.y})`,tabindex:'0',role:'button','data-node-id':node.node_id,'aria-label':`Memory ${node.id}: ${node.title}${node.pinned?' (pinned)':''}`}),halo=svg('circle',{class:'node-halo',r:radius+7}),core=svg('circle',{class:'node-core',r:radius});g.style.setProperty('--proj-color',node.project?colorForProject(node.project):'#9aa6ff');g.style.setProperty('--comm',communityColor(node.node_id));g.classList.toggle('is-hub',(Number(node.edge_count)||0)>=state.hubThreshold);g.append(halo,core);if(node.pinned){const ring=svg('circle',{class:'pin-ring',r:radius+4});g.append(ring);const pin=svg('text',{class:'pin-mark',x:radius-3,y:-radius+3,'aria-hidden':'true'});pin.textContent='◉';g.append(pin)}const text=svg('text',{class:'node-label',x:radius+9,y:'4'});text.textContent=(node.title.length>32?node.title.slice(0,31)+'…':node.title);g.append(text);if(state.selected===node.node_id)attachSatellites(g,node);g.addEventListener('pointerenter',()=>focusGraphNode(node.node_id,true));g.addEventListener('pointerleave',()=>{state.hovered=null;applyGraphFocus()});g.addEventListener('focus',()=>focusGraphNode(node.node_id,true));g.addEventListener('blur',()=>{state.hovered=null;applyGraphFocus()});g.addEventListener('pointerdown',e=>beginNodeDrag(e,node.node_id));g.addEventListener('click',()=>{if(state.suppressClick){state.suppressClick=false;return}select(node.node_id)});g.addEventListener('dblclick',e=>{e.preventDefault();select(node.node_id);focusNode(node.node_id)});g.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();select(node.node_id)}else if(e.key==='f'){e.preventDefault();focusNode(node.node_id)}});return g}
  // Graphify-style satellite attachment: a selected memory's linked code/doc
  // files orbit the node as small chips, so attached scope is visible at a glance
  // without leaving the graph. Chips live inside the node <g>, so they follow
  // drag/pan automatically via the group transform.
  function attachSatellites(g,node){const detail=state.detail;if(!detail||detail.memory.node_id!==node.node_id)return;const files=[];for(const f of String(detail.memory.code_files||'').split(/[\n,]/).map(s=>s.trim()).filter(Boolean))files.push({kind:'code',label:f});for(const f of String(detail.memory.doc_files||'').split(/[\n,]/).map(s=>s.trim()).filter(Boolean))files.push({kind:'doc',label:f});if(!files.length)return;const radius=nodeRadius(node)+8,count=files.length,n=Math.min(count,8),step=(Math.PI*2)/n;for(let i=0;i<n;i++){const a=i*step-Math.PI/2,x=Math.cos(a)*radius,y=Math.sin(a)*radius,chip=svg('g',{class:`satellite ${files[i].kind}`,transform:`translate(${x} ${y})`});const dot=svg('circle',{class:'sat-dot',r:4.5});const tag=svg('text',{class:'sat-label',x:Math.sign(x)*7||7,y:'3.5'});tag.textContent=files[i].label.length>16?files[i].label.slice(0,15)+'…':files[i].label;chip.append(dot,tag);chip.addEventListener('click',e=>{e.stopPropagation();select(node.node_id)});g.append(chip)}if(count>n){const more=svg('text',{class:'sat-more',x:0,y:radius+14});more.textContent=`+${count-n} more`;g.append(more)}}
  function graphNodeElement(id){return Array.from(nodeLayer.children).find(el=>el.dataset.nodeId===id)}
  function graphEdgeElement(id){return Array.from(edgeLayer.children).find(el=>Number(el.dataset.edgeId)===Number(id))}
  function syncGraphPositions(ids=null){const changed=ids?new Set(ids):null;for(const el of nodeLayer.children){if(changed&&!changed.has(el.dataset.nodeId))continue;const p=state.positions.get(el.dataset.nodeId);if(p)el.setAttribute('transform',`translate(${p.x} ${p.y})`)}for(const el of edgeLayer.children){if(changed&&!changed.has(el.dataset.source)&&!changed.has(el.dataset.target))continue;const edge=state.edges.find(item=>Number(item.id)===Number(el.dataset.edgeId)),c=edge&&edgeCoordinates(edge);if(!c)continue;const d=edgePathD(c);for(const path of el.querySelectorAll('path'))path.setAttribute('d',d)}}
  function applyGraphFocus(){const focus=state.hovered||state.selected,cluster=focus?connectedSet(focus):new Set();for(const el of nodeLayer.children){const id=el.dataset.nodeId;el.classList.toggle('selected',id===state.selected);el.classList.toggle('hovered',id===state.hovered);el.classList.toggle('neighbor',Boolean(focus&&cluster.has(id)));el.classList.toggle('cluster',Boolean(focus&&cluster.has(id)&&id!==focus));el.classList.toggle('dim',Boolean(state.settings.dimNeighbors&&focus&&id!==focus&&!cluster.has(id)))}for(const el of edgeLayer.children){const connected=Boolean(focus&&cluster.has(el.dataset.source)&&cluster.has(el.dataset.target));el.classList.toggle('is-focus',connected);el.classList.toggle('is-dim',Boolean(state.settings.dimNeighbors&&focus&&!connected))}const node=focus&&nodeById(focus);$('#graph-selection').textContent=node?`#${node.id} ${node.title}${cluster.size>1?` · ${cluster.size} connected`:''}`:'Drag canvas to pan · scroll to zoom'}
  function focusGraphNode(id,hovered=false){if(hovered)state.hovered=id;applyGraphFocus()}
  function updateGraphMeta(){const visible=visibleNodeIds(),links=state.edges.filter(e=>visible.has(e.source)&&visible.has(e.target)).length;$('#graph-summary').textContent=`${visible.size} ${visible.size===1?'memory':'memories'} · ${links} ${links===1?'link':'links'}`;$('#graph-empty').hidden=visible.size!==0}
  function patchEdgesForNode(id){const visible=visibleNodeIds();Array.from(edgeLayer.children).filter(el=>el.dataset.source===id||el.dataset.target===id).forEach(el=>el.remove());for(const edge of state.edges){if(edge.source!==id&&edge.target!==id)continue;const element=makeEdgeElement(edge,visible);if(element)edgeLayer.append(element)}syncGraphPositions([id])}
  function patchGraphNode(id){ensurePositions();const node=nodeById(id),visible=visibleNodeIds(),existing=graphNodeElement(id);if(!node||!visible.has(id)){if(existing)existing.remove();patchEdgesForNode(id);updateGraphMeta();applyGraphFocus();return}const replacement=makeNodeElement(node);if(existing)existing.replaceWith(replacement);else nodeLayer.append(replacement);patchEdgesForNode(id);updateGraphMeta();applyGraphFocus()}
  function patchGraphEdge(id){ensurePositions();const existing=graphEdgeElement(id);if(existing)existing.remove();const edge=state.edges.find(item=>Number(item.id)===Number(id));if(edge){const element=makeEdgeElement(edge);if(element)edgeLayer.append(element)}updateGraphMeta();applyGraphFocus()}
  function removeGraphNode(id){const node=graphNodeElement(id);if(node)node.remove();Array.from(edgeLayer.children).filter(el=>el.dataset.source===id||el.dataset.target===id).forEach(el=>el.remove());state.positions.delete(id);state.fixed.delete(id);updateGraphMeta()}
  // ForceAtlas2-flavoured layout (what graphify's viewer uses): degree-weighted
  // repulsion so hubs carve out space, hard collision padding so nodes never
  // overlap, spring links, and gravity toward the node's own cluster territory
  // instead of one global centre — separate clusters stay visually separate.
  // Runs entirely client-side — no CDN, no worker.
  function forceTick(nodes,edgeRefs){const spacing=Math.max(80,state.settings.linkDistance),size=spacing*1.4,buckets=new Map();nodes.forEach((node,index)=>{const p=state.positions.get(node.node_id),key=`${Math.floor(p.x/size)},${Math.floor(p.y/size)}`;if(!buckets.has(key))buckets.set(key,[]);buckets.get(key).push(index)});const repel=state.settings.charge*3.2;nodes.forEach((node,index)=>{const p=state.positions.get(node.node_id),cellX=Math.floor(p.x/size),cellY=Math.floor(p.y/size);for(let ox=-1;ox<=1;ox++)for(let oy=-1;oy<=1;oy++)for(const otherIndex of buckets.get(`${cellX+ox},${cellY+oy}`)||[]){if(otherIndex<=index)continue;const other=nodes[otherIndex],q=state.positions.get(other.node_id);let dx=q.x-p.x,dy=q.y-p.y,d2=dx*dx+dy*dy;if(d2<1e-4){dx=Math.random()-.5;dy=Math.random()-.5;d2=dx*dx+dy*dy}const d=Math.sqrt(d2),mass=Math.sqrt(((Number(node.edge_count)||0)+1)*((Number(other.edge_count)||0)+1)),minimum=nodeRadius(node)+nodeRadius(other)+20,push=repel*mass/d+(d<minimum?(minimum-d)*0.45:0),px=dx/d*push,py=dy/d*push;if(!state.fixed.has(node.node_id)){node.vx-=px;node.vy-=py}if(!state.fixed.has(other.node_id)){other.vx+=px;other.vy+=py}}const home=state.clusterCenters.get(node.node_id);if(home&&!state.fixed.has(node.node_id)){node.vx+=(home.x-p.x)*0.02;node.vy+=(home.y-p.y)*0.02}});for(const edge of edgeRefs){const source=edge.source,target=edge.target,a=state.positions.get(source.node_id),b=state.positions.get(target.node_id),dx=b.x-a.x||.01,dy=b.y-a.y||.01,d=Math.hypot(dx,dy)||.01,pull=(d-state.settings.linkDistance)*0.06,px=dx/d*pull,py=dy/d*pull;if(!state.fixed.has(source.node_id)){source.vx+=px;source.vy+=py}if(!state.fixed.has(target.node_id)){target.vx-=px;target.vy-=py}}for(const node of nodes){if(state.fixed.has(node.node_id)){node.vx=0;node.vy=0;continue}const p=state.positions.get(node.node_id);node.vx*=.84;node.vy*=.84;const speed=Math.hypot(node.vx,node.vy),cap=speed>9?9/speed:1;p.x+=node.vx*cap;p.y+=node.vy*cap}}
  function runForceLayout(reset=false){ensurePositions(reset);if(state.animation)cancelAnimationFrame(state.animation);const nodes=state.nodes.map(node=>({...node,vx:0,vy:0})),byId=new Map(nodes.map(node=>[node.node_id,node])),edgeRefs=state.edges.map(edge=>({source:byId.get(edge.source),target:byId.get(edge.target)})).filter(edge=>edge.source&&edge.target),reduced=matchMedia('(prefers-reduced-motion: reduce)').matches,total=nodes.length>350?260:400;let tick=0;$('#graph-selection').textContent='Settling graph…';const finish=()=>{state.animation=null;syncGraphPositions();applyGraphFocus();if(!state.viewTouched)fitGraph()};if(reduced){for(;tick<total;tick++)forceTick(nodes,edgeRefs);finish();return}const frame=()=>{for(let i=0;i<3&&tick<total;i++,tick++)forceTick(nodes,edgeRefs);syncGraphPositions();if(tick<total)state.animation=requestAnimationFrame(frame);else finish()};state.animation=requestAnimationFrame(frame)}
  function fitGraph(){const ids=visibleNodeIds(),points=[...ids].map(id=>state.positions.get(id)).filter(Boolean);if(!points.length)return;const minX=Math.min(...points.map(p=>p.x)),maxX=Math.max(...points.map(p=>p.x)),minY=Math.min(...points.map(p=>p.y)),maxY=Math.max(...points.map(p=>p.y)),r=graph.getBoundingClientRect(),ratio=Math.max(r.width,1)/Math.max(r.height,1),padding=100;let w=Math.max(320,maxX-minX+padding*2),h=Math.max(224,maxY-minY+padding*2);if(w/h<ratio)w=h*ratio;else h=w/ratio;state.view={x:(minX+maxX-w)/2,y:(minY+maxY-h)/2,w,h};updateView()}
  function focusNode(id){const p=state.positions.get(id);if(!p)return;const r=graph.getBoundingClientRect(),ratio=Math.max(r.width,1)/Math.max(r.height,1),w=Math.min(620,Math.max(300,state.view.w*.58)),h=w/ratio;state.view={x:p.x-w/2,y:p.y-h/2,w,h};state.viewTouched=true;updateView()}
  function renderGraph(){computeCommunities();ensurePositions();edgeLayer.replaceChildren();nodeLayer.replaceChildren();const visible=visibleNodeIds();for(const edge of state.edges){const element=makeEdgeElement(edge,visible);if(element)edgeLayer.append(element)}for(const node of state.nodes){if(visible.has(node.node_id))nodeLayer.append(makeNodeElement(node))}syncGraphPositions();updateGraphMeta();applyGraphFocus();if(!state.layoutReady){state.layoutReady=true;requestAnimationFrame(()=>runForceLayout(false))}}
  function makeMemoryRow(node){const row=document.createElement('button');row.className=`memory-row ${state.selected===node.node_id?'selected':''}`;row.type='button';row.dataset.nodeId=node.node_id;const title=document.createElement('span');title.className='memory-title';if(node.pinned){const ic=document.createElement('span');ic.className='pin-ic';ic.textContent='◉';ic.setAttribute('aria-hidden','true');title.append(ic)}const t=document.createElement('span');t.textContent=`#${node.id} ${node.title}`;title.append(t);const badge=document.createElement('span');badge.className='badge';badge.textContent=node.status==='active'?node.type:node.status;const meta=document.createElement('span');meta.className='memory-meta';meta.textContent=`${node.node_id} · ${node.agent} · ↔${node.edge_count}`;const preview=document.createElement('span');preview.className='preview';preview.textContent=node.preview;row.append(title,badge,meta,preview);row.addEventListener('click',()=>select(node.node_id));return row}
  function listRowElement(id){return Array.from(memoryList.children).find(el=>el.dataset?.nodeId===id)}
  function updateListStatus(){const nodes=visibleNodes(),empty=memoryList.querySelector('[data-empty-list]');$('#result-count').textContent=`${nodes.length}/${state.nodes.length}`;if(nodes.length){if(empty)empty.remove()}else if(!empty){const message=document.createElement('div');message.className='empty';message.dataset.emptyList='true';message.textContent='No memories match the current search and filters.';memoryList.append(message)}return nodes}
  function patchListNode(id){const node=nodeById(id),existing=listRowElement(id),visible=visibleNodeIds().has(id),scroll=memoryList.scrollTop,hadFocus=document.activeElement===existing;if(!node||!visible){if(existing)existing.remove();updateListStatus();memoryList.scrollTop=scroll;return}const replacement=makeMemoryRow(node);if(existing)existing.replaceWith(replacement);else{const index=state.nodes.findIndex(item=>item.node_id===id),anchor=state.nodes.slice(index+1).map(item=>listRowElement(item.node_id)).find(Boolean),empty=memoryList.querySelector('[data-empty-list]');memoryList.insertBefore(replacement,anchor||empty)}updateListStatus();memoryList.scrollTop=scroll;if(hadFocus)replacement.focus({preventScroll:true})}
  function removeListNode(id){const row=listRowElement(id),scroll=memoryList.scrollTop;if(row)row.remove();updateListStatus();memoryList.scrollTop=scroll}
  function renderList(rebuildGraph=true){memoryList.replaceChildren();for(const node of visibleNodes())memoryList.append(makeMemoryRow(node));updateListStatus();if(rebuildGraph)renderGraph()}
  function relationship(edge, detail){const outgoing=edge.source_node_id===detail.memory.node_id, other=outgoing?edge.target_node_id:edge.source_node_id, otherId=outgoing?edge.target_id:edge.source_id, otherTitle=outgoing?edge.target_title:edge.source_title;const b=document.createElement('button');b.className='relationship';b.type='button';const dir=document.createElement('span');dir.className='direction';dir.textContent=outgoing?'→ OUT':'← IN';const text=document.createElement('span');text.textContent=`#${otherId} ${otherTitle} · ${edge.edge_type}`;const reason=document.createElement('span');reason.className='reason';reason.textContent=edge.metadata?`— ${edge.metadata}`:'';b.append(dir,text,reason);b.addEventListener('click',()=>select(other));return b}
  function renderInspector(){const root=$('#inspector');root.replaceChildren();const d=state.detail;if(!d){const e=document.createElement('div');e.className='empty';e.textContent=state.selected?'Loading memory…':'Select a memory in the graph or list to read it in full.';root.append(e);return}const m=d.memory, header=document.createElement('div');header.className='memory-header';const titleBox=document.createElement('div'), h=document.createElement('h1');h.textContent=`#${m.id} ${m.title}`;const id=document.createElement('div');id.className='small';id.textContent=m.node_id;titleBox.append(h,id);const actions=document.createElement('div');actions.className='actions';const pinBtn=document.createElement('button');pinBtn.type='button';pinBtn.className='pin-btn'+(m.pinned?' on':'');pinBtn.textContent=m.pinned?'◉ Pinned':'◯ Pin';pinBtn.setAttribute('aria-pressed',String(Boolean(m.pinned)));pinBtn.addEventListener('click',()=>togglePin(m.node_id,!m.pinned));const edit=document.createElement('button');edit.textContent='Edit';edit.addEventListener('click',openEdit);const link=document.createElement('button');link.textContent='Link';link.addEventListener('click',openLink);actions.append(pinBtn,edit,link);if(m.status==='active'){const archive=document.createElement('button');archive.textContent='Archive';archive.addEventListener('click',openArchive);actions.append(archive)}header.append(titleBox,actions);root.append(header);const metadata=document.createElement('div');metadata.className='metadata';for(const text of [m.event_type,m.status,m.agent_type,m.project?'project':'global',m.pinned?'pinned':'',`created ${new Date(m.created_at).toLocaleString()}`])if(text){const v=document.createElement('span');v.className='meta'+(text==='pinned'?' pinned':'');v.textContent=text;metadata.append(v)}root.append(metadata);if(m.archive_reason){const note=document.createElement('div');note.className='archive-note';note.textContent=`Archive reason: ${m.archive_reason}`;root.append(note)}const content=document.createElement('article');content.className='content';content.textContent=m.content;root.append(content);const fileCols=document.createElement('div');fileCols.className='file-cols';fileCols.append(fileCard('Code files',m.code_files),fileCard('Doc files',m.doc_files));root.append(fileCols);const heading=document.createElement('h2');heading.className='section-title';heading.innerHTML=`Linked memories <span class="count">${d.edges.length}</span>`;root.append(heading);if(d.edges.length){const out=d.edges.filter(e=>e.source_node_id===m.node_id), inc=d.edges.filter(e=>e.target_node_id===m.node_id);if(out.length){const sub=document.createElement('div');sub.className='small';sub.textContent='Outgoing';root.append(sub);for(const e of out)root.append(relationship(e,d),unlinkBtn(e))}if(inc.length){const sub=document.createElement('div');sub.className='small';sub.textContent='Incoming';root.append(sub);for(const e of inc)root.append(relationship(e,d),unlinkBtn(e))}}else{const empty=document.createElement('div');empty.className='empty';empty.textContent='This memory is an orphan. Link it to preserve graph context.';root.append(empty)}if(d.chain?.length){const cHead=document.createElement('h2');cHead.className='section-title';cHead.textContent='Supersession chain';root.append(cHead);const chain=document.createElement('div');chain.textContent=d.chain.map(x=>`#${x.id} ${x.title} (${x.status})`).join(' → ');root.append(chain)}}
  function fileCard(label,value){const card=document.createElement('div');card.className='file-card '+(value?'filled':'empty');const head=document.createElement('div');head.className='fc-head';const name=document.createElement('span');name.textContent=label;const state=document.createElement('span');state.className='fc-state';state.textContent=value?'filled':'empty';head.append(name,state);const body=document.createElement('div');body.className='fc-body';body.textContent=value||'No files recorded by agents';card.append(head,body);return card}
  function unlinkBtn(edge){const b=document.createElement('button');b.className='relationship unlink';b.type='button';b.textContent='Unlink';b.addEventListener('click',()=>unlink(edge));return b}
  async function select(ref){const node=nodeById(ref)||nodeById(String(ref));if(!node){notice('Linked memory is outside this dashboard scope.');return}const previous=state.selected;state.selected=node.node_id;state.detail=null;if(previous){patchGraphNode(previous);patchListNode(previous)}patchGraphNode(node.node_id);patchListNode(node.node_id);renderInspector();try{const detail=await api(`/api/memories/${encodeURIComponent(node.node_id)}`);if(state.selected===node.node_id){state.detail=detail;renderInspector();patchGraphNode(node.node_id)}}catch(e){notice(e.message)}}
  function updateStats(stats){for(const [key,label] of [['orphans','orphans'],['stale_tasks','stale tasks'],['contradictions','contradictions']])document.querySelector(`[data-health="${key==='stale_tasks'?'stale':key}"]`).textContent=`${stats[key]} ${label}`;const s=$('#status');s.textContent=`Synced · ${stats.search_mode}`;s.className='status online'}
  function replaceNode(node){const i=state.nodes.findIndex(n=>n.node_id===node.node_id);if(i<0)state.nodes.unshift(node);else state.nodes[i]=node}
  function showDraftConflict(){const form=$('#edit-form');let banner=form.querySelector('.draft-conflict');if(!banner){banner=document.createElement('div');banner.className='archive-note draft-conflict';const text=document.createElement('span');text.textContent='This memory changed elsewhere. Your draft is safe.';const reload=document.createElement('button');reload.type='button';reload.textContent='Reload';reload.addEventListener('click',async()=>{try{const d=await api(`/api/memories/${encodeURIComponent(state.selected)}`);state.detail=d;form.title.value=d.memory.title;form.content.value=d.memory.content;state.conflict=false;banner.hidden=true;renderInspector()}catch(e){notice(e.message)}});const keep=document.createElement('button');keep.type='button';keep.textContent='Keep editing';keep.addEventListener('click',()=>{banner.hidden=true});banner.append(text,reload,keep);form.insertBefore(banner,form.querySelector('.form-error'))}banner.hidden=false}
  $('#edit-dialog').addEventListener('close',()=>{const conflicted=state.conflict;state.conflict=false;const banner=$('#edit-form .draft-conflict');if(banner)banner.hidden=true;if(conflicted&&state.selected)refreshNode(state.selected)});
  async function refreshNode(id){try{const summary=await api(`/api/memories/${encodeURIComponent(id)}/summary`);replaceNode(summary);patchGraphNode(id);patchListNode(id);const editDialog=$('#edit-dialog');if(state.selected===id&&editDialog.open){state.conflict=true;showDraftConflict();return}if(state.selected===id&&!state.conflict)await select(id)}catch(e){if(e.message.includes('not found')){state.nodes=state.nodes.filter(n=>n.node_id!==id);state.edges=state.edges.filter(x=>x.source!==id&&x.target!==id);removeGraphNode(id);removeListNode(id);if(state.selected===id){state.selected=null;state.detail=null;notice('The selected memory was deleted.')}renderInspector()}else notice(e.message)}}
  async function refreshStats(){try{updateStats(await api('/api/stats'))}catch(e){$('#status').textContent='Connection problem';$('#status').className='status'}}
  async function loadSnapshot(preserve=true){const snap=await api('/api/snapshot');const old=new Map(state.positions);state.nodes=snap.nodes;state.edges=snap.edges;state.revision=snap.revision;state.positions=old;$('#scope').textContent=snap.scope_label;$('#memory-form [name=agent]').value=snap.default_agent||'user';updateStats(snap.stats);renderList();if(state.selected&&state.nodes.some(n=>n.node_id===state.selected)&&!state.conflict)select(state.selected)}
  function connectEvents(){if(state.eventSource)state.eventSource.close();const source=state.eventSource=new EventSource(`/events?since_revision=${state.revision}`);source.onopen=()=>{const s=$('#status');s.textContent='Synced';s.className='status online'};source.onerror=()=>{$('#status').textContent='Reconnecting…';$('#status').className='status'};for(const kind of ['memory.created','memory.updated','memory.archived'])source.addEventListener(kind,e=>{const p=JSON.parse(e.data);state.revision=Math.max(state.revision,p.revision);refreshNode(p.node_id);refreshStats()});source.addEventListener('memory.deleted',e=>{const p=JSON.parse(e.data);state.revision=Math.max(state.revision,p.revision);refreshNode(p.node_id);refreshStats()});source.addEventListener('edge.created',e=>{const p=JSON.parse(e.data);state.revision=Math.max(state.revision,p.revision);if(!state.edges.some(x=>x.id===p.edge_id))state.edges.push({id:p.edge_id,source:p.source,target:p.target,type:p.type,reason:''});patchGraphEdge(p.edge_id);refreshNode(p.source);refreshNode(p.target);if(state.selected===p.source||state.selected===p.target)select(state.selected);refreshStats()});source.addEventListener('edge.deleted',e=>{const p=JSON.parse(e.data);state.revision=Math.max(state.revision,p.revision);state.edges=state.edges.filter(x=>x.id!==p.edge_id);patchGraphEdge(p.edge_id);refreshNode(p.source);refreshNode(p.target);if(state.selected===p.source||state.selected===p.target)select(state.selected);refreshStats()});source.addEventListener('reset',async e=>{const p=JSON.parse(e.data);state.revision=p.revision;notice('Live history expired; refreshing the graph once.');await loadSnapshot();});}
  async function applyMutation(result){if(result.memory){replaceNode(result.memory);patchGraphNode(result.memory.node_id);patchListNode(result.memory.node_id);await select(result.memory.node_id);return}if(result.edge_id&&result.source&&result.target){if(!result.duplicate&&!state.edges.some(edge=>Number(edge.id)===Number(result.edge_id)))state.edges.push({id:result.edge_id,source:result.source,target:result.target,type:result.edge_type,reason:''});patchGraphEdge(result.edge_id);await Promise.all([refreshNode(result.source),refreshNode(result.target)])}}
  async function togglePin(node_id,pinned){try{const result=await api(`/api/memories/${encodeURIComponent(node_id)}/pin`,{method:'POST',body:JSON.stringify({pinned})});const node=nodeById(node_id);if(node)node.pinned=result.memory.pinned;replaceNode(result.memory);patchGraphNode(node_id);patchListNode(node_id);if(state.selected===node_id){state.detail.memory.pinned=result.memory.pinned;renderInspector()}notice(result.memory.pinned?'Memory pinned.':'Memory unpinned.')}catch(e){notice(e.message)}}
  $('#create').addEventListener('click',()=>$('#memory-dialog').showModal());$('#memory-form').addEventListener('submit',e=>{e.preventDefault();submit(e.currentTarget,'/api/memories')});
  function openEdit(){const m=state.detail.memory,f=$('#edit-form');f.title.value=m.title;f.content.value=m.content;f.querySelector('.form-error').textContent='';f.closest('dialog').showModal()}
  $('#edit-form').addEventListener('submit',e=>{e.preventDefault();submit(e.currentTarget,`/api/memories/${encodeURIComponent(state.selected)}`,'PUT')});
  function openArchive(){const f=$('#archive-form');f.reset();f.querySelector('.form-error').textContent='';f.closest('dialog').showModal()}
  $('#archive-form').addEventListener('submit',e=>{e.preventDefault();submit(e.currentTarget,`/api/memories/${encodeURIComponent(state.selected)}/archive`)});
  function openLink(){const f=$('#link-form');f.reset();f.source.value=state.selected;f.querySelector('.form-error').textContent='';f.closest('dialog').showModal()}
  $('#link-form').addEventListener('submit',e=>{e.preventDefault();submit(e.currentTarget,'/api/edges')});
  async function unlink(edge){if(!confirm(`Unlink e${edge.id}? This can create an orphan.`))return;try{const result=await api(`/api/edges/${edge.id}`,{method:'DELETE'});state.edges=state.edges.filter(item=>Number(item.id)!==Number(result.edge_id));patchGraphEdge(result.edge_id);await Promise.all([refreshNode(result.source),refreshNode(result.target)]);if(state.selected)await select(state.selected);notice('Relationship removed.')}catch(e){notice(e.message)}}
  let searchTimer;$('#search').addEventListener('input',()=>{const input=$('#search'),term=input.value.trim();clearTimeout(searchTimer);state.searchNodeIds=null;renderList();if(!term)return;searchTimer=setTimeout(async()=>{try{const results=await api(`/api/search?q=${encodeURIComponent(term)}`);if(input.value.trim()!==term)return;state.searchNodeIds=new Set(results.map(result=>result.node_id));results.forEach(replaceNode);renderList();if(results.length===1)focusNode(results[0].node_id)}catch(e){notice(e.message)}},180)});  document.querySelectorAll('#type-filter,#agent-filter,#project-filter,#pinned-filter').forEach(control=>control.addEventListener('change',renderList));$('#status-filter').addEventListener('change',()=>{const note=$('#filter-note'),v=$('#status-filter').value;note.hidden=!v;note.textContent=v==='orphans'?'Orphans have no relationships. Link them to preserve context.':v==='archived'?'Archived knowledge remains readable and searchable.':'';renderList()});
  document.querySelectorAll('[data-health]').forEach(b=>b.addEventListener('click',()=>{const type=b.dataset.health;if(type==='orphans')$('#status-filter').value='orphans';else if(type==='stale'){$('#type-filter').value='task';$('#status-filter').value='active'}else{$('#search').value='';$('#type-filter').value='';$('#status-filter').value='';notice('Contradiction edges are shown as red dashed links.')}renderList()}));
  function zoomAt(clientX,clientY,factor){const before=clientToGraph(clientX,clientY),r=graph.getBoundingClientRect(),ratio=Math.max(r.width,1)/Math.max(r.height,1),newW=Math.max(260,Math.min(3600,state.view.w*factor)),newH=newW/ratio,rx=(clientX-r.left)/Math.max(r.width,1),ry=(clientY-r.top)/Math.max(r.height,1);state.view={x:before.x-rx*newW,y:before.y-ry*newH,w:newW,h:newH};state.viewTouched=true;updateView()}
  function zoomFromCenter(factor){const r=graph.getBoundingClientRect();zoomAt(r.left+r.width/2,r.top+r.height/2,factor)}
  function updateNodeScale(){state.settings.nodeScale=Number($('#node-scale').value)/100;$('#node-scale-value').textContent=`${Math.round(state.settings.nodeScale*100)}%`;renderGraph()}
  $('#label-mode').addEventListener('change',e=>{state.settings.labelMode=e.target.value;graphStage.dataset.labelMode=e.target.value;updateView()});
  $('#show-arrows').addEventListener('change',e=>{state.settings.showArrows=e.target.checked;renderGraph()});
  $('#dim-neighbors').addEventListener('change',e=>{state.settings.dimNeighbors=e.target.checked;applyGraphFocus()});
  $('#node-scale').addEventListener('input',updateNodeScale);
  $('#link-distance').addEventListener('input',e=>{state.settings.linkDistance=Number(e.target.value);$('#link-distance-value').textContent=e.target.value});
  $('#repel-force').addEventListener('input',e=>{state.settings.charge=Number(e.target.value);$('#repel-force-value').textContent=e.target.value});
  $('#zoom-in').addEventListener('click',()=>zoomFromCenter(.82));$('#zoom-out').addEventListener('click',()=>zoomFromCenter(1.22));$('#fit-graph').addEventListener('click',()=>{fitGraph();state.viewTouched=true});
  $('#relayout').addEventListener('click',()=>{if(confirm('Re-layout graph? Dragged positions will be released and the graph will settle again.')){state.viewTouched=false;ensurePositions(true);renderGraph();runForceLayout(false)}});
  document.addEventListener('pointerdown',e=>{const settings=$('#graph-settings');if(settings.open&&!settings.contains(e.target))settings.removeAttribute('open')});
  graph.addEventListener('pointerdown',e=>{if(e.button!==0||e.target.closest('.node'))return;state.pan={pointerId:e.pointerId,clientX:e.clientX,clientY:e.clientY,view:{...state.view}};graph.classList.add('panning');graph.setPointerCapture(e.pointerId)});
  graph.addEventListener('pointermove',e=>{if(state.nodeDrag&&state.nodeDrag.pointerId===e.pointerId){const p=clientToGraph(e.clientX,e.clientY),position=state.positions.get(state.nodeDrag.id);if(!position)return;position.x=p.x;position.y=p.y;state.nodeDrag.moved=state.nodeDrag.moved||Math.hypot(p.x-state.nodeDrag.startX,p.y-state.nodeDrag.startY)>4;syncGraphPositions([state.nodeDrag.id]);focusGraphNode(state.nodeDrag.id,true);return}if(!state.pan||state.pan.pointerId!==e.pointerId)return;const r=graph.getBoundingClientRect(),dx=(e.clientX-state.pan.clientX)*state.pan.view.w/Math.max(r.width,1),dy=(e.clientY-state.pan.clientY)*state.pan.view.h/Math.max(r.height,1);state.view={...state.pan.view,x:state.pan.view.x-dx,y:state.pan.view.y-dy};state.viewTouched=true;updateView()});
  function finishPointer(event){if(state.nodeDrag&&state.nodeDrag.pointerId===event.pointerId){const drag=state.nodeDrag,id=drag.id,moved=drag.moved;state.nodeDrag=null;state.suppressClick=true;if(moved){patchGraphNode(id);notice('Node position pinned. Re-layout to release it.')}else{if(!drag.wasFixed)state.fixed.delete(id);select(id)}setTimeout(()=>state.suppressClick=false,0)}if(state.pan&&state.pan.pointerId===event.pointerId)state.pan=null;graph.classList.remove('panning')}
  graph.addEventListener('pointerup',finishPointer);graph.addEventListener('pointercancel',finishPointer);graph.addEventListener('lostpointercapture',finishPointer);
  graph.addEventListener('wheel',e=>{e.preventDefault();zoomAt(e.clientX,e.clientY,e.deltaY>0?1.12:.89)},{passive:false});
  graph.addEventListener('dblclick',e=>{if(e.target.closest('.node'))return;fitGraph();state.viewTouched=true});
  // Obsidian-inspired graph workspace enhancements stay dependency-free and
  // layer on top of the live SVG renderer, so SSE patches keep their identity.
  const graphEnhancementStyle=document.createElement('style');
  graphEnhancementStyle.textContent=`
    .graph-wrap{--link-width:1.6}
    .graph-stage[data-label-mode=auto] .node.is-hub .node-label{opacity:1}
    .graph-stage::after{content:"";position:absolute;inset:0;z-index:0;pointer-events:none;box-shadow:inset 0 0 90px #0008}
    #graph{z-index:1}
    .edge-line{stroke-width:var(--link-width);stroke-linecap:round}
    .edge.is-focus .edge-line{stroke-width:calc(var(--link-width) + 1px)}
    .node .node-core{filter:drop-shadow(0 1px 2px #000b) drop-shadow(0 0 7px color-mix(in srgb,currentColor 18%,transparent))}
    .node.selected .node-core{filter:drop-shadow(0 0 2px #fff) drop-shadow(0 0 12px #9b8cffb8)}
    .node.is-orphan .node-core{stroke-dasharray:2.5 3.5}
    .node.local-hidden,.edge.local-hidden{display:none}
    .graph-control-dock{position:absolute;z-index:9;top:12px;bottom:44px;left:12px;width:min(290px,calc(100% - 24px));display:flex;flex-direction:column;overflow:hidden;border:1px solid #ffffff1c;border-radius:11px;background:#11141ef2;color:var(--graph-text);box-shadow:0 18px 50px #000a;backdrop-filter:blur(16px);opacity:0;pointer-events:none;transform:translateX(calc(-100% - 20px));transition:transform .2s ease,opacity .2s ease}
    .graph-control-dock.open{opacity:1;pointer-events:auto;transform:translateX(0)}
    .graph-control-head{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:11px 12px;border-bottom:1px solid #ffffff14}
    .graph-control-head strong{font-size:13px;letter-spacing:.01em}
    .graph-control-head button{padding:3px 7px;border-color:#ffffff18;background:transparent;color:var(--graph-muted);font-size:11px}
    .graph-control-dock .graph-settings-panel{position:static;inset:auto;width:auto;min-height:0;overflow:auto;padding:12px;border:0;border-radius:0;background:transparent;color:var(--graph-text);box-shadow:none;display:grid;gap:12px}
    .graph-control-dock .graph-setting{color:var(--graph-text)}
    .graph-control-dock .graph-setting select{border-color:#ffffff20;background:#171b28;color:var(--graph-text)}
    .graph-control-dock .graph-setting input[type=range]{accent-color:var(--graph-accent)}
    .graph-control-section{display:grid;gap:10px;padding-bottom:12px;border-bottom:1px solid #ffffff14}
    .graph-control-section h3{margin:0;color:#fff;font-size:11px;text-transform:uppercase;letter-spacing:.09em}
    .graph-control-action{width:100%;border-color:#ffffff1c;background:#ffffff08;color:var(--graph-text);font-size:12px}
    .graph-control-shortcuts{margin:0;color:var(--graph-muted);font-size:10px;line-height:1.5}
    .graph-stage.controls-open .graph-hud{left:314px}
    .graph-stage.controls-open .graph-legend{left:314px}
    .graph-hud,.graph-legend{z-index:5;transition:left .2s ease}
    .graph-legend{right:12px;flex-wrap:wrap;row-gap:5px}
    .graph-legend i.graph-type-dot{box-shadow:0 0 0 2px color-mix(in srgb,var(--dot-color) 24%,transparent);background:var(--dot-color)}
    .graph-wrap.graph-expanded{position:fixed;inset:0;z-index:1000;height:100vh;border:0;border-radius:0;box-shadow:none}
    .graph-wrap.graph-expanded .panel-head{height:56px;padding-inline:14px;background:color-mix(in srgb,var(--panel) 94%,transparent);backdrop-filter:blur(12px)}
    .graph-wrap.graph-expanded .graph-stage{height:calc(100vh - 56px)}
    body.graph-mode{overflow:hidden}
    @media(max-width:720px){#fit-graph,#relayout{display:none}.graph-actions>#expand-graph,.graph-settings>summary{padding-inline:6px;font-size:11px}.graph-stage.controls-open .graph-hud,.graph-stage.controls-open .graph-legend{left:12px;opacity:.18}.graph-control-dock{bottom:12px}.graph-legend{max-height:34px;overflow:hidden}}
    .scope-switch{display:flex;align-items:center;gap:6px;margin-left:2px}
    .scope-switch-label{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.08em}
    .scope-switch select{border:1px solid var(--line);border-radius:7px;background:var(--bg);color:var(--ink);padding:.4rem .5rem;font-size:12px;max-width:200px}
    .node.cluster .node-core{stroke:var(--proj-color,#9b8cff);stroke-width:3;stroke-opacity:1}
    .graph-legend .graph-scope-dot{box-shadow:0 0 0 2px color-mix(in srgb,var(--dot-color) 26%,transparent);background:var(--dot-color)}
  `;
  document.head.append(graphEnhancementStyle);

  const graphPreferencesKey='trailmem.graph.preferences.v1';
  const readGraphPreferences=()=>{try{return JSON.parse(localStorage.getItem(graphPreferencesKey)||'{}')}catch{return{}}};
  const savedGraphPreferences=readGraphPreferences();
  const graphUi={
    depth:['all','1','2','3'].includes(savedGraphPreferences.depth)?savedGraphPreferences.depth:'all',
    showOrphans:savedGraphPreferences.showOrphans!==false,
    adaptiveNodeSize:savedGraphPreferences.adaptiveNodeSize!==false,
    linkThickness:Math.max(.6,Math.min(3,Number(savedGraphPreferences.linkThickness)||1.6)),
    expanded:false
  };
  const clampPreference=(value,min,max,fallback)=>{const number=Number(value);return Number.isFinite(number)?Math.max(min,Math.min(max,number)):fallback};
  if(['auto','always','hover','off'].includes(savedGraphPreferences.labelMode))state.settings.labelMode=savedGraphPreferences.labelMode;
  if(typeof savedGraphPreferences.showArrows==='boolean')state.settings.showArrows=savedGraphPreferences.showArrows;
  if(typeof savedGraphPreferences.dimNeighbors==='boolean')state.settings.dimNeighbors=savedGraphPreferences.dimNeighbors;
  state.settings.nodeScale=clampPreference(savedGraphPreferences.nodeScale,.75,1.5,state.settings.nodeScale);
  state.settings.linkDistance=clampPreference(savedGraphPreferences.linkDistance,80,420,state.settings.linkDistance);
  state.settings.charge=clampPreference(savedGraphPreferences.charge,1,20,state.settings.charge);
  $('#label-mode').value=state.settings.labelMode;
  $('#show-arrows').checked=state.settings.showArrows;
  $('#dim-neighbors').checked=state.settings.dimNeighbors;
  $('#node-scale').value=Math.round(state.settings.nodeScale*100);
  $('#node-scale-value').textContent=`${Math.round(state.settings.nodeScale*100)}%`;
  $('#link-distance').value=Math.round(state.settings.linkDistance);
  $('#link-distance-value').textContent=Math.round(state.settings.linkDistance);
  $('#repel-force').value=Math.round(state.settings.charge);
  $('#repel-force-value').textContent=Math.round(state.settings.charge);
  graphStage.dataset.labelMode=state.settings.labelMode;
  graphStage.style.setProperty('--link-width',`${graphUi.linkThickness}px`);

  const graphWrap=document.querySelector('.graph-wrap'),settingsDetails=$('#graph-settings'),settingsSummary=settingsDetails.querySelector('summary'),settingsPanel=settingsDetails.querySelector('.graph-settings-panel');
  const controlsDock=document.createElement('aside');controlsDock.className='graph-control-dock';controlsDock.setAttribute('aria-label','Graph controls');controlsDock.setAttribute('aria-hidden','true');
  const controlsHead=document.createElement('div');controlsHead.className='graph-control-head';
  const controlsTitle=document.createElement('strong');controlsTitle.textContent='Graph controls';
  const controlsClose=document.createElement('button');controlsClose.type='button';controlsClose.textContent='Close';controlsClose.setAttribute('aria-label','Close graph controls');
  controlsHead.append(controlsTitle,controlsClose);
  const localControls=document.createElement('section');localControls.className='graph-control-section';
  localControls.innerHTML=`<h3>Local graph</h3><label class="graph-setting">Neighborhood<select id="local-depth"><option value="all">All memories</option><option value="1">1 hop</option><option value="2">2 hops</option><option value="3">3 hops</option></select></label><label class="graph-setting"><span>Show orphans</span><input id="show-orphans" type="checkbox"></label><label class="graph-setting"><span>Scale by links</span><input id="adaptive-node-size" type="checkbox"></label><label class="graph-setting graph-setting-range"><span>Link width</span><input id="link-thickness" type="range" min="0.6" max="3" value="1.6" step="0.15"><output id="link-thickness-value">1.6</output></label><button id="center-selection" class="graph-control-action" type="button">Center selected memory</button><p class="graph-control-shortcuts">Shortcuts: / search · 0 fit · +/− zoom · Shift+F expand · Esc back</p>`;
  settingsPanel.prepend(localControls);controlsDock.append(controlsHead,settingsPanel);graphStage.append(controlsDock);
  settingsSummary.textContent='Controls';settingsSummary.setAttribute('aria-expanded','false');
  $('#local-depth').value=graphUi.depth;$('#show-orphans').checked=graphUi.showOrphans;$('#adaptive-node-size').checked=graphUi.adaptiveNodeSize;$('#link-thickness').value=graphUi.linkThickness;$('#link-thickness-value').textContent=graphUi.linkThickness.toFixed(2).replace(/0$/,'');
  const expandGraphButton=document.createElement('button');expandGraphButton.type='button';expandGraphButton.id='expand-graph';expandGraphButton.textContent='Expand';expandGraphButton.title='Open immersive graph view (Shift+F)';expandGraphButton.setAttribute('aria-pressed','false');settingsDetails.before(expandGraphButton);

  function saveGraphPreferences(){try{localStorage.setItem(graphPreferencesKey,JSON.stringify({depth:graphUi.depth,showOrphans:graphUi.showOrphans,adaptiveNodeSize:graphUi.adaptiveNodeSize,linkThickness:graphUi.linkThickness,labelMode:state.settings.labelMode,showArrows:state.settings.showArrows,dimNeighbors:state.settings.dimNeighbors,nodeScale:state.settings.nodeScale,linkDistance:state.settings.linkDistance,charge:state.settings.charge}))}catch{}}
  function setControlsOpen(open){controlsDock.classList.toggle('open',open);controlsDock.setAttribute('aria-hidden',String(!open));graphStage.classList.toggle('controls-open',open);settingsSummary.setAttribute('aria-expanded',String(open));if(open)controlsClose.focus({preventScroll:true})}
  function setGraphExpanded(expanded){graphUi.expanded=expanded;graphWrap.classList.toggle('graph-expanded',expanded);document.body.classList.toggle('graph-mode',expanded);expandGraphButton.textContent=expanded?'Exit':'Expand';expandGraphButton.setAttribute('aria-pressed',String(expanded));requestAnimationFrame(()=>{updateView();fitGraph()})}
  settingsSummary.addEventListener('click',event=>{event.preventDefault();settingsDetails.open=false;setControlsOpen(!controlsDock.classList.contains('open'))});
  controlsClose.addEventListener('click',()=>setControlsOpen(false));
  expandGraphButton.addEventListener('click',()=>setGraphExpanded(!graphUi.expanded));
  document.addEventListener('pointerdown',event=>{if(controlsDock.classList.contains('open')&&!controlsDock.contains(event.target)&&!settingsDetails.contains(event.target))setControlsOpen(false)});

  // Link-rich memories become visual anchors, as in Obsidian, without allowing
  // hubs to overwhelm smaller clusters.
  nodeRadius=function(node){const degree=Math.max(0,Number(node?.edge_count)||0),degreeBoost=graphUi.adaptiveNodeSize?16*Math.min(1,degree/Math.max(1,state.maxDegree)):3,pinnedBoost=node?.pinned?2:0;return(8+degreeBoost+pinnedBoost)*state.settings.nodeScale};
  function baseGraphIds(){const ids=new Set(visibleNodes().map(node=>node.node_id));if(!graphUi.showOrphans)for(const node of state.nodes)if(Number(node.edge_count)===0)ids.delete(node.node_id);return ids}
  function localGraphIds(){const base=baseGraphIds();if(graphUi.depth==='all'||!state.selected||!base.has(state.selected))return base;const allowed=new Set([state.selected]),queue=[[state.selected,0]],neighbors=adjacency(),limit=Number(graphUi.depth);while(queue.length){const [id,depth]=queue.shift();if(depth>=limit)continue;for(const neighbor of neighbors.get(id)||[]){if(!base.has(neighbor)||allowed.has(neighbor))continue;allowed.add(neighbor);queue.push([neighbor,depth+1])}}return allowed}
  function applyLocalGraph(){const shown=localGraphIds();for(const element of nodeLayer.children)element.classList.toggle('local-hidden',!shown.has(element.dataset.nodeId));let links=0;for(const element of edgeLayer.children){const hidden=!shown.has(element.dataset.source)||!shown.has(element.dataset.target);element.classList.toggle('local-hidden',hidden);if(!hidden)links++}const local=graphUi.depth!=='all'&&state.selected;$('#graph-summary').textContent=`${shown.size} ${shown.size===1?'memory':'memories'} · ${links} ${links===1?'link':'links'}${local?' · local':''}`;$('#graph-empty').hidden=shown.size!==0;$('#local-depth').title=state.selected?'':'Select a memory to activate a local neighborhood.';return shown}
  function fitVisibleGraph(){const ids=applyLocalGraph(),points=[...ids].map(id=>state.positions.get(id)).filter(Boolean);if(!points.length)return;const minX=Math.min(...points.map(point=>point.x)),maxX=Math.max(...points.map(point=>point.x)),minY=Math.min(...points.map(point=>point.y)),maxY=Math.max(...points.map(point=>point.y)),rect=graph.getBoundingClientRect(),ratio=Math.max(rect.width,1)/Math.max(rect.height,1),padding=82;let width=Math.max(300,maxX-minX+padding*2),height=Math.max(210,maxY-minY+padding*2);if(width/height<ratio)width=height*ratio;else height=width/ratio;state.view={x:(minX+maxX-width)/2,y:(minY+maxY-height)/2,w:width,h:height};updateView()}
  fitGraph=fitVisibleGraph;

  // ---- Scope switcher: switch project/global scope at runtime, no reload ----
  const projectColors={__global:'#8b93a7'};
  const projectPalette=['#6c8cff','#49b98a','#ef6173','#e5a84b','#b47bea','#38bdb5','#df70aa','#7dd3fc','#f59e0b','#34d399'];
  let projectIndex=0;
  function colorForProject(project){const key=project||'__global';if(!projectColors[key])projectColors[key]=projectPalette[projectIndex++%projectPalette.length];return projectColors[key]}
  function applyProjectColors(){for(const element of nodeLayer.children){const node=nodeById(element.dataset.nodeId);if(!node)continue;element.style.setProperty('--proj-color',colorForProject(node.project))}}
  const scopeSwitch=$('#scope-switch');
  const projectFilter=$('#project-filter');
  async function loadScopeList(){try{const data=await api('/api/scope-list');scopeSwitch.replaceChildren();projectFilter.replaceChildren();const allOpt=document.createElement('option');allOpt.value='';allOpt.textContent='All projects';projectFilter.append(allOpt);for(const scope of data.scopes){const option=document.createElement('option');option.value=scope.key;option.textContent=`${scope.label} (${scope.count})`;scopeSwitch.append(option);if(scope.key!=='all'&&scope.key!=='global'){const po=document.createElement('option');po.value=scope.key;po.textContent=scope.label;projectFilter.append(po)}}scopeSwitch.value=data.current;const colorMap={};for(const scope of data.scopes)if(scope.key!=='all'&&scope.key!=='global')colorForProject(scope.key);applyProjectColors()}catch(e){}}
  scopeSwitch.addEventListener('change',async()=>{try{await api('/api/scope',{method:'POST',body:JSON.stringify({scope:scopeSwitch.value})});await loadSnapshot(false);applyProjectColors();notice(`Scope: ${scopeSwitch.options[scopeSwitch.selectedIndex].textContent}`)}catch(e){notice(e.message)}});
  const decorateProjectBase=decorateGraph;decorateGraph=function(){applyProjectColors();return decorateProjectBase()};
  const graphLegend=document.querySelector('.graph-legend'),typeNames={decision:'decision',lesson:'lesson',error_pattern:'error pattern',task:'task',memory:'memory',user_preference:'preference',constraint:'constraint',session_summary:'summary'};
  let legendSignature='';
  function decorateGraph(){computeCommunities();const shown=applyLocalGraph();for(const element of nodeLayer.children){const node=nodeById(element.dataset.nodeId);if(!node)continue;element.classList.toggle('is-orphan',Number(node.edge_count)===0);element.classList.toggle('is-hub',(Number(node.edge_count)||0)>=state.hubThreshold);element.style.setProperty('--comm',communityColor(node.node_id));element.dataset.type=node.type;let title=element.querySelector('title');if(!title){title=svg('title');element.prepend(title)}title.textContent=`#${node.id} ${node.title}\n${typeNames[node.type]||node.type} · ${node.edge_count} ${Number(node.edge_count)===1?'link':'links'}${node.pinned?' · pinned':''}`}
    for(const element of edgeLayer.children)element.style.setProperty('--edge-color',communityColor(element.dataset.source));
    const clusters=(state.clusterLegend||[]).filter(c=>c.size>1&&shown.has(c.hub)).slice(0,8);const signature=clusters.map(c=>`${c.color}·${c.hub}·${c.size}`).join('|');if(signature!==legendSignature){legendSignature=signature;graphLegend.replaceChildren();for(const c of clusters){const item=document.createElement('span'),dot=document.createElement('i');dot.className='graph-type-dot';dot.style.setProperty('--dot-color',c.color);const hub=nodeById(c.hub),name=hub?hub.title:'cluster';item.append(dot,document.createTextNode(`${name.length>18?name.slice(0,17)+'…':name} · ${c.size}`));graphLegend.append(item)}const pin=document.createElement('span');pin.className='legend-pin';pin.textContent='◉ pinned';graphLegend.append(pin)}}
  let decorateFrame=null;
  function scheduleGraphDecoration(){if(decorateFrame!==null)return;decorateFrame=requestAnimationFrame(()=>{decorateFrame=null;decorateGraph()})}
  const graphObserver=new MutationObserver(scheduleGraphDecoration);graphObserver.observe(nodeLayer,{childList:true});graphObserver.observe(edgeLayer,{childList:true});
  const selectBase=select;select=function(ref){const result=selectBase(ref);scheduleGraphDecoration();return result};

  function clearGraphSelection(){const previous=state.selected;if(!previous)return;state.selected=null;state.detail=null;patchGraphNode(previous);patchListNode(previous);renderInspector();applyGraphFocus();scheduleGraphDecoration()}
  let backgroundPress=null;
  graph.addEventListener('pointerdown',event=>{if(event.button===0&&!event.target.closest('.node')&&!event.target.closest('.edge'))backgroundPress={id:event.pointerId,x:event.clientX,y:event.clientY}});
  graph.addEventListener('pointerup',event=>{if(!backgroundPress||backgroundPress.id!==event.pointerId)return;const clicked=Math.hypot(event.clientX-backgroundPress.x,event.clientY-backgroundPress.y)<4;backgroundPress=null;if(clicked)clearGraphSelection()});
  graph.addEventListener('pointercancel',()=>{backgroundPress=null});

  $('#local-depth').addEventListener('change',event=>{graphUi.depth=event.target.value;saveGraphPreferences();applyLocalGraph();fitGraph()});
  $('#show-orphans').addEventListener('change',event=>{graphUi.showOrphans=event.target.checked;saveGraphPreferences();applyLocalGraph();fitGraph()});
  $('#adaptive-node-size').addEventListener('change',event=>{graphUi.adaptiveNodeSize=event.target.checked;saveGraphPreferences();renderGraph();scheduleGraphDecoration()});
  $('#link-thickness').addEventListener('input',event=>{graphUi.linkThickness=Number(event.target.value);graphStage.style.setProperty('--link-width',`${graphUi.linkThickness}px`);$('#link-thickness-value').textContent=graphUi.linkThickness.toFixed(2).replace(/0$/,'')});
  $('#link-thickness').addEventListener('change',saveGraphPreferences);
  $('#center-selection').addEventListener('click',()=>state.selected?focusNode(state.selected):fitGraph());
  for(const control of ['#label-mode','#show-arrows','#dim-neighbors','#node-scale','#link-distance','#repel-force'])$(control).addEventListener('change',saveGraphPreferences);
  document.addEventListener('keydown',event=>{if(event.defaultPrevented||event.metaKey||event.ctrlKey||event.altKey)return;const tag=event.target?.tagName?.toLowerCase(),editing=['input','textarea','select'].includes(tag)||event.target?.isContentEditable;if(event.key==='Escape'){if(document.querySelector('dialog[open]'))return;if(controlsDock.classList.contains('open'))setControlsOpen(false);else if(graphUi.expanded)setGraphExpanded(false);else clearGraphSelection();return}if(editing)return;if(event.key==='/'){event.preventDefault();$('#search').focus()}else if(event.key==='0'){event.preventDefault();fitGraph()}else if(event.key==='+'||event.key==='='){event.preventDefault();zoomFromCenter(.82)}else if(event.key==='-'){event.preventDefault();zoomFromCenter(1.22)}else if(event.shiftKey&&event.key.toLowerCase()==='f'){event.preventDefault();setGraphExpanded(!graphUi.expanded)}});

  new ResizeObserver(()=>updateView()).observe(graphStage);updateView();
  (async()=>{try{await loadSnapshot(false);await loadScopeList();decorateGraph();connectEvents()}catch(e){$('#status').textContent=`Could not load: ${e.message}`;$('#status').className='status';notice(e.message)}})();
})();
</script>
</body></html>'''
