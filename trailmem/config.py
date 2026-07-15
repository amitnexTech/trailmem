"""Config + paths. ~/.trailmem/ holds config.json, trailmem.db, models/."""

import json
import os
from pathlib import Path

TRAILMEM_HOME = Path(os.environ.get("TRAILMEM_HOME", Path.home() / ".trailmem"))
CONFIG_PATH = TRAILMEM_HOME / "config.json"
MODELS_DIR = TRAILMEM_HOME / "models"

DEFAULT_CONFIG = {
    "embedding": {
        "enabled": True,
        "model": "bge-small",
        "dimensions": 384,
        # Per-model dedup bands — cosine distributions differ across models.
        "dedup_warn": 0.85,
        "dedup_block": 0.92,
    },
}


def db_path() -> Path:
    return Path(os.environ.get("TRAILMEM_DB", TRAILMEM_HOME / "trailmem.db"))


def load_config() -> dict:
    if CONFIG_PATH.exists():
        cfg = json.loads(CONFIG_PATH.read_text())
        merged = {**DEFAULT_CONFIG, **cfg}
        merged["embedding"] = {**DEFAULT_CONFIG["embedding"], **cfg.get("embedding", {})}
        return merged
    return json.loads(json.dumps(DEFAULT_CONFIG))


def save_config(cfg: dict) -> None:
    TRAILMEM_HOME.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")
