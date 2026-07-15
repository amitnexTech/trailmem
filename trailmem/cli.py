"""trailmem CLI. Full command surface in docs/cli.md — built incrementally."""

import argparse
import sys

from . import __version__
from .config import CONFIG_PATH, TRAILMEM_HOME, db_path, load_config, save_config
from .schema import connect, has_vec, init_db


def cmd_setup(args) -> int:
    TRAILMEM_HOME.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        save_config(load_config())
        print(f"wrote {CONFIG_PATH}")
    conn = connect()
    init_db(conn)
    conn.close()
    print(f"database ready: {db_path()}")
    print("NOTE: embedding model download + MCP registration not implemented yet")
    return 0


def cmd_doctor(args) -> int:
    ok = True
    cfg = load_config()
    print(f"home:   {TRAILMEM_HOME} {'✓' if TRAILMEM_HOME.exists() else '✗ (run trailmem setup)'}")
    print(f"config: {CONFIG_PATH} {'✓' if CONFIG_PATH.exists() else '✗'}")
    if not db_path().exists():
        print(f"db:     {db_path()} ✗ (run trailmem setup)")
        return 1
    conn = connect()
    tables = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")
    }
    for t in ("memories", "edges", "sessions", "memories_fts"):
        present = t in tables
        ok &= present
        print(f"table:  {t} {'✓' if present else '✗'}")
    vec = has_vec(conn)
    if cfg["embedding"]["enabled"]:
        print(f"vec:    sqlite-vec {'✓' if vec else '✗ DEGRADED — FTS-only, near-dup detection OFF'}")
        print(f"model:  {cfg['embedding']['model']} ({cfg['embedding']['dimensions']}d) — install check not implemented yet")
    else:
        print("vec:    disabled by config — FTS-only mode (exact-hash dedup only)")
    conn.close()
    return 0 if ok else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="trailmem", description="Graph-linked persistent memory for AI coding agents")
    parser.add_argument("--version", action="version", version=f"trailmem {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("setup", help="Create ~/.trailmem/, init DB").set_defaults(func=cmd_setup)
    sub.add_parser("doctor", help="Health check: DB, tables, vec, model").set_defaults(func=cmd_doctor)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
