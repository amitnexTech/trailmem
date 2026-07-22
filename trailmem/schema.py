"""SQLite schema + connection setup.

All the SQLite traps live here:
- foreign_keys must be ON per-connection or ON DELETE CASCADE silently fails.
- memories_vec / memories_fts are virtual tables — they do NOT cascade;
  app code deletes from all three explicitly.
- memories_vec dims come from config (float[N]), never hardcoded.
"""

import sqlite3
from pathlib import Path

from .config import db_path, load_config

MEMORIES = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    event_type TEXT NOT NULL DEFAULT 'memory',
    work_type TEXT,
    agent_type TEXT NOT NULL,
    project TEXT,
    session_id TEXT,
    source_uri TEXT,
    code_files TEXT,
    doc_files TEXT,
    pinned INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT,
    access_count INTEGER NOT NULL DEFAULT 0,
    last_accessed TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    archive_reason TEXT,
    content_hash TEXT NOT NULL
)
"""

EDGES = """
CREATE TABLE IF NOT EXISTS edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_node_id TEXT NOT NULL,
    target_node_id TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    metadata TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (source_node_id) REFERENCES memories(node_id) ON DELETE CASCADE,
    FOREIGN KEY (target_node_id) REFERENCES memories(node_id) ON DELETE CASCADE
)
"""

SESSIONS = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    agent_type TEXT NOT NULL,
    project TEXT,
    started_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_welcome_at TEXT,
    write_count INTEGER NOT NULL DEFAULT 0,
    last_write_at TEXT
)
"""

FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    node_id UNINDEXED,
    title,
    content
)
"""

# Dashboard revisions are written by SQLite triggers, so CLI/MCP/dashboard
# mutations all reach the same revisioned SSE feed after their transaction.
DASHBOARD_STATE = """
CREATE TABLE IF NOT EXISTS dashboard_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    revision INTEGER NOT NULL DEFAULT 0
)
"""

DASHBOARD_EVENTS = """
CREATE TABLE IF NOT EXISTS dashboard_events (
    revision INTEGER PRIMARY KEY,
    event TEXT NOT NULL,
    node_id TEXT,
    data TEXT
)
"""

DASHBOARD_TRIGGERS = [
    """
    CREATE TRIGGER IF NOT EXISTS dashboard_memory_created
    AFTER INSERT ON memories
    BEGIN
        UPDATE dashboard_state SET revision = revision + 1 WHERE id = 1;
        INSERT INTO dashboard_events (revision, event, node_id)
        SELECT revision, 'memory.created', NEW.node_id FROM dashboard_state WHERE id = 1;
        DELETE FROM dashboard_events
        WHERE revision < (SELECT revision - 1000 FROM dashboard_state WHERE id = 1);
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS dashboard_memory_updated
    AFTER UPDATE OF title, content, event_type, pinned, status, archive_reason ON memories
    WHEN NEW.title IS NOT OLD.title
      OR NEW.content IS NOT OLD.content
      OR NEW.event_type IS NOT OLD.event_type
      OR NEW.pinned IS NOT OLD.pinned
      OR NEW.status IS NOT OLD.status
      OR NEW.archive_reason IS NOT OLD.archive_reason
    BEGIN
        UPDATE dashboard_state SET revision = revision + 1 WHERE id = 1;
        INSERT INTO dashboard_events (revision, event, node_id)
        SELECT revision,
               CASE WHEN NEW.status IS NOT 'active' AND OLD.status IS 'active'
                    THEN 'memory.archived' ELSE 'memory.updated' END,
               NEW.node_id
        FROM dashboard_state WHERE id = 1;
        DELETE FROM dashboard_events
        WHERE revision < (SELECT revision - 1000 FROM dashboard_state WHERE id = 1);
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS dashboard_memory_deleted
    AFTER DELETE ON memories
    BEGIN
        UPDATE dashboard_state SET revision = revision + 1 WHERE id = 1;
        INSERT INTO dashboard_events (revision, event, node_id)
        SELECT revision, 'memory.deleted', OLD.node_id FROM dashboard_state WHERE id = 1;
        DELETE FROM dashboard_events
        WHERE revision < (SELECT revision - 1000 FROM dashboard_state WHERE id = 1);
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS dashboard_edge_created
    AFTER INSERT ON edges
    BEGIN
        UPDATE dashboard_state SET revision = revision + 1 WHERE id = 1;
        INSERT INTO dashboard_events (revision, event, node_id, data)
        SELECT revision, 'edge.created', NEW.source_node_id,
               NEW.source_node_id || char(9) || NEW.target_node_id || char(9) || NEW.edge_type || char(9) || NEW.id
        FROM dashboard_state WHERE id = 1;
        DELETE FROM dashboard_events
        WHERE revision < (SELECT revision - 1000 FROM dashboard_state WHERE id = 1);
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS dashboard_edge_deleted
    AFTER DELETE ON edges
    BEGIN
        UPDATE dashboard_state SET revision = revision + 1 WHERE id = 1;
        INSERT INTO dashboard_events (revision, event, node_id, data)
        SELECT revision, 'edge.deleted', OLD.source_node_id,
               OLD.source_node_id || char(9) || OLD.target_node_id || char(9) || OLD.edge_type || char(9) || OLD.id
        FROM dashboard_state WHERE id = 1;
        DELETE FROM dashboard_events
        WHERE revision < (SELECT revision - 1000 FROM dashboard_state WHERE id = 1);
    END;
    """,
]

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_memories_hash_project ON memories(content_hash, project)",
    "CREATE INDEX IF NOT EXISTS idx_memories_status_pinned ON memories(status, pinned)",
    "CREATE INDEX IF NOT EXISTS idx_memories_project ON memories(project)",
    "CREATE INDEX IF NOT EXISTS idx_memories_event_type ON memories(event_type)",
    "CREATE INDEX IF NOT EXISTS idx_memories_agent ON memories(agent_type)",
    "CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_node_id)",
    "CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_node_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_edges_unique ON edges(source_node_id, target_node_id, edge_type)",
]


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Open a connection with the mandatory pragmas and sqlite-vec loaded."""
    p = path or db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 3000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.row_factory = sqlite3.Row
    try:
        import sqlite_vec

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except (ImportError, sqlite3.OperationalError):
        pass  # FTS-only degraded mode; doctor flags it
    return conn


def vec_table_sql(dimensions: int) -> str:
    return (
        "CREATE VIRTUAL TABLE IF NOT EXISTS memories_vec USING vec0("
        f"node_id TEXT, embedding float[{dimensions}] distance_metric=cosine)"
    )


def has_vec(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("SELECT vec_version()")
        return True
    except sqlite3.OperationalError:
        return False


# Post-publish schema migrations. CREATE TABLE/TRIGGER IF NOT EXISTS above
# already covers NEW objects; this hook exists for changes to EXISTING tables
# (e.g. ALTER TABLE ... ADD COLUMN). Each entry runs at most once, in order,
# tracked via PRAGMA user_version. Append only — never edit or reorder shipped
# entries. Example: "ALTER TABLE memories ADD COLUMN foo TEXT".
MIGRATIONS: list[str] = [
    # 1: split modified_files into code_files + doc_files. Two named fields
    # prompt agents to record BOTH source and doc edits (a single generic
    # field got only doc paths in practice). Existing data lands in code_files.
    "ALTER TABLE memories RENAME COLUMN modified_files TO code_files;\n"
    "ALTER TABLE memories ADD COLUMN doc_files TEXT;",
    # 2: session save-awareness must count successful edits as well as creates.
    # Existing zero rows remain NULL = unknown so pre-fix PID/UUID splits do not
    # produce false "saved 0" warnings after upgrade.
    "ALTER TABLE sessions ADD COLUMN write_count INTEGER;\n"
    "ALTER TABLE sessions ADD COLUMN last_write_at TEXT;\n"
    "UPDATE sessions SET write_count = ("
    "SELECT COUNT(*) FROM memories m WHERE m.session_id = sessions.session_id"
    ") WHERE EXISTS ("
    "SELECT 1 FROM memories m WHERE m.session_id = sessions.session_id"
    ");",
]


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for step, sql in enumerate(MIGRATIONS[version:], start=version + 1):
        conn.executescript(sql)
        conn.execute(f"PRAGMA user_version = {step}")
    conn.commit()


def init_db(conn: sqlite3.Connection) -> None:
    """Create all tables + indexes. Idempotent."""
    cfg = load_config()
    # A fresh DB is created at the CURRENT schema — mark all migrations as
    # applied, or migrate() would re-run ALTERs against already-new columns.
    fresh = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memories'"
    ).fetchone() is None
    conn.execute(MEMORIES)
    if fresh:
        conn.execute(f"PRAGMA user_version = {len(MIGRATIONS)}")
    conn.execute(EDGES)
    conn.execute(SESSIONS)
    conn.execute(FTS)
    conn.execute(DASHBOARD_STATE)
    conn.execute(DASHBOARD_EVENTS)
    conn.execute("INSERT OR IGNORE INTO dashboard_state (id, revision) VALUES (1, 0)")
    for trigger in DASHBOARD_TRIGGERS:
        conn.executescript(trigger)
    if cfg["embedding"]["enabled"] and has_vec(conn):
        conn.execute(vec_table_sql(cfg["embedding"]["dimensions"]))
    for idx in INDEXES:
        conn.execute(idx)
    migrate(conn)
    conn.commit()
