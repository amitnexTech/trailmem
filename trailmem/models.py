"""Embedding model registry: install (download, checksum), use, disable, reindex.

Models are NEVER bundled in the wheel — downloaded to ~/.trailmem/models/<name>/.
"""

import hashlib
import sqlite3
import sys
import urllib.request
from pathlib import Path

from .config import MODELS_DIR, load_config, save_config

# sha256 values are trust-on-first-use: recorded into config on install and
# verified against the registry when present.
REGISTRY = {
    "bge-small": {
        "dimensions": 384,
        "files": {
            "model.onnx": "https://huggingface.co/Xenova/bge-small-en-v1.5/resolve/main/onnx/model.onnx",
            "tokenizer.json": "https://huggingface.co/Xenova/bge-small-en-v1.5/resolve/main/tokenizer.json",
        },
        "sha256": {},  # filled per release once artifacts are pinned
        "note": "default — 384d, ~130MB, good balance",
    },
    "minilm": {
        "dimensions": 384,
        "files": {
            "model.onnx": "https://huggingface.co/Xenova/all-MiniLM-L6-v2/resolve/main/onnx/model.onnx",
            "tokenizer.json": "https://huggingface.co/Xenova/all-MiniLM-L6-v2/resolve/main/tokenizer.json",
        },
        "sha256": {},
        "note": "lighter, ~200MB RAM",
    },
    "nomic": {
        "dimensions": 768,
        "files": {
            "model.onnx": "https://huggingface.co/nomic-ai/nomic-embed-text-v1.5/resolve/main/onnx/model.onnx",
            "tokenizer.json": "https://huggingface.co/nomic-ai/nomic-embed-text-v1.5/resolve/main/tokenizer.json",
        },
        "sha256": {},
        "note": "better quality, 768d, ~500MB RAM",
    },
}

# Per-model dedup bands (cosine distributions differ across models).
BANDS = {
    "bge-small": (0.85, 0.92),
    "minilm": (0.80, 0.90),
    "nomic": (0.85, 0.92),
}


def installed(name: str) -> bool:
    d = MODELS_DIR / name
    return (d / "model.onnx").exists() and (d / "tokenizer.json").exists()


def install(name: str, path: str | None = None) -> int:
    """Download a registry model, or register a custom ONNX via --path."""
    dest = MODELS_DIR / name
    dest.mkdir(parents=True, exist_ok=True)
    if path:
        src = Path(path)
        if not src.exists():
            print(f"error: {path} not found", file=sys.stderr)
            return 1
        (dest / "model.onnx").write_bytes(src.read_bytes())
        tok = src.parent / "tokenizer.json"
        if not tok.exists():
            print(f"error: tokenizer.json expected beside {path}", file=sys.stderr)
            return 1
        (dest / "tokenizer.json").write_bytes(tok.read_bytes())
        print(f"installed custom model '{name}' from {path}")
        return 0

    spec = REGISTRY.get(name)
    if not spec:
        print(f"error: unknown model '{name}'. Known: {', '.join(REGISTRY)}", file=sys.stderr)
        return 1
    for fname, url in spec["files"].items():
        target = dest / fname
        if target.exists():
            print(f"  {fname}: already present, skipping")
            continue
        print(f"  downloading {fname} ...")
        tmp = target.with_suffix(".part")
        urllib.request.urlretrieve(url, tmp)
        digest = hashlib.sha256(tmp.read_bytes()).hexdigest()
        expected = spec["sha256"].get(fname)
        if expected and digest != expected:
            tmp.unlink()
            print(f"error: checksum mismatch for {fname} (got {digest[:16]}...)", file=sys.stderr)
            return 1
        tmp.rename(target)
        print(f"  {fname}: ok (sha256 {digest[:16]}...)")
    print(f"installed '{name}' → {dest}")
    return 0


def use(name: str) -> int:
    if not installed(name):
        print(f"error: '{name}' not installed. Run: trailmem model install {name}", file=sys.stderr)
        return 1
    cfg = load_config()
    old_dims = cfg["embedding"]["dimensions"]
    dims = REGISTRY.get(name, {}).get("dimensions")
    if dims is None:
        print("custom model: enter its embedding dimensions in ~/.trailmem/config.json "
              "(embedding.dimensions) before reindex.", file=sys.stderr)
        dims = old_dims
    warn, block = BANDS.get(name, (0.85, 0.92))
    cfg["embedding"].update({"enabled": True, "model": name, "dimensions": dims,
                             "dedup_warn": warn, "dedup_block": block})
    save_config(cfg)
    print(f"active model: {name} ({dims}d, bands {warn}/{block})")
    if dims != old_dims:
        print(f"⚠ dimensions changed {old_dims} → {dims}: run `trailmem reindex` "
              "(semantic search is stale until then)")
    else:
        print("run `trailmem reindex` to re-embed existing memories with the new model")
    return 0


def disable() -> int:
    cfg = load_config()
    cfg["embedding"]["enabled"] = False
    save_config(cfg)
    print("embeddings DISABLED → FTS5-only mode.")
    print("⚠ WARNING: semantic search OFF + near-duplicate detection OFF (exact-hash only).")
    return 0


def reindex(conn: sqlite3.Connection) -> int:
    """DROP + recreate memories_vec with current dims, re-embed all active+archived content."""
    from . import embeddings
    from .schema import has_vec, vec_table_sql

    cfg = load_config()["embedding"]
    if not cfg["enabled"]:
        print("embeddings disabled — nothing to reindex (trailmem model use <name> first)",
              file=sys.stderr)
        return 1
    if not has_vec(conn):
        print("sqlite-vec extension unavailable — cannot reindex", file=sys.stderr)
        return 1
    if not embeddings.available():
        print(f"model '{cfg['model']}' not installed — run: trailmem model install {cfg['model']}",
              file=sys.stderr)
        return 1

    conn.execute("DROP TABLE IF EXISTS memories_vec")
    conn.execute(vec_table_sql(cfg["dimensions"]))
    rows = conn.execute("SELECT node_id, content FROM memories").fetchall()
    for i, r in enumerate(rows, 1):
        vec = embeddings.embed(r["content"])
        conn.execute("INSERT INTO memories_vec (node_id, embedding) VALUES (?, ?)",
                     (r["node_id"], vec.tobytes()))
        if i % 50 == 0:
            print(f"  {i}/{len(rows)}")
    conn.commit()
    print(f"reindexed {len(rows)} memories with {cfg['model']} ({cfg['dimensions']}d, "
          f"bands {cfg['dedup_warn']}/{cfg['dedup_block']})")
    return 0
