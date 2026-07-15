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
    modified_files TEXT,
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
    last_welcome_at TEXT
)
"""

FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    node_id UNINDEXED,
    title,
    content
)
"""

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


def init_db(conn: sqlite3.Connection) -> None:
    """Create all tables + indexes. Idempotent."""
    cfg = load_config()
    conn.execute(MEMORIES)
    conn.execute(EDGES)
    conn.execute(SESSIONS)
    conn.execute(FTS)
    if cfg["embedding"]["enabled"] and has_vec(conn):
        conn.execute(vec_table_sql(cfg["embedding"]["dimensions"]))
    for idx in INDEXES:
        conn.execute(idx)
    conn.commit()
