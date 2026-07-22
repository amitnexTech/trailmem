"""Migration 2 preserves uncertainty for legacy session save accounting."""

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trailmem.schema import migrate  # noqa: E402


def run() -> None:
    path = Path(tempfile.gettempdir()) / "tm-session-migration.db"
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY,
            session_id TEXT
        );
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY,
            agent_type TEXT NOT NULL,
            project TEXT,
            started_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_welcome_at TEXT
        );
        INSERT INTO sessions VALUES
            ('codex:known-write', 'codex', '/tmp/p', '1', '1', NULL),
            ('pid-legacy-zero', 'codex', '/tmp/p', '2', '2', NULL);
        INSERT INTO memories (session_id) VALUES ('codex:known-write');
        PRAGMA user_version = 1;
        """
    )
    migrate(conn)
    rows = {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT session_id, write_count FROM sessions ORDER BY session_id"
        )
    }
    assert rows["codex:known-write"] == 1
    assert rows["pid-legacy-zero"] is None
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    conn.close()
    path.unlink(missing_ok=True)
    print("SESSION MIGRATION OK")


if __name__ == "__main__":
    run()
