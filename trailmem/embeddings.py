"""Lazy ONNX embedding. Model absent/disabled → embed() returns None (FTS-only mode).

No hash-embedding pseudo-vectors — degrade loudly, never fake similarity.
"""

import sys
from pathlib import Path

from .config import MODELS_DIR, load_config

_session = None
_tokenizer = None
_broken = False  # onnxruntime failed to load once — stay in FTS-only mode


def _model_dir() -> Path:
    return MODELS_DIR / load_config()["embedding"]["model"]


def available() -> bool:
    cfg = load_config()
    if not cfg["embedding"]["enabled"]:
        return False
    d = _model_dir()
    return (d / "model.onnx").exists() and (d / "tokenizer.json").exists()


def detect_dims(model_dir) -> int:
    """Embed a probe string with the model at model_dir and return its output
    dimensionality. Used at custom-model install time so the user never has to
    look up and hand-enter dims. Independent of the active-model config."""
    import numpy as np
    import onnxruntime
    from tokenizers import Tokenizer

    d = Path(model_dir)
    session = onnxruntime.InferenceSession(str(d / "model.onnx"))
    tokenizer = Tokenizer.from_file(str(d / "tokenizer.json"))
    enc = tokenizer.encode("dimension probe")
    inputs = {
        "input_ids": np.array([enc.ids], dtype=np.int64),
        "attention_mask": np.array([enc.attention_mask], dtype=np.int64),
    }
    if any(i.name == "token_type_ids" for i in session.get_inputs()):
        inputs["token_type_ids"] = np.array([enc.type_ids], dtype=np.int64)
    out = session.run(None, inputs)[0]  # (1, seq, dim)
    return int(out.shape[-1])


def embed(text: str):
    """Return a normalized float32 vector, or None when embeddings are unavailable.

    numpy/onnxruntime/tokenizers are imported here, not at module top —
    FTS-only mode must work without the embedding deps installed.
    """
    global _session, _tokenizer, _broken
    if _broken or not available():
        return None
    try:
        import numpy as np

        if _session is None:
            # available() checks config+files only; the import itself can die
            # (Windows: onnxruntime DLL init fails with WinError 1114 even when
            # installed). Degrade to FTS-only instead of killing store/query.
            import onnxruntime
            from tokenizers import Tokenizer

            d = _model_dir()
            _session = onnxruntime.InferenceSession(str(d / "model.onnx"))
            _tokenizer = Tokenizer.from_file(str(d / "tokenizer.json"))
            _tokenizer.enable_truncation(max_length=512)
    except Exception as e:
        _broken = True
        # ASCII-only message: this can fire before console.configure() on a
        # cp1252 stderr — the warning itself must never be the crash.
        print(f"trailmem: embedding runtime unavailable ({e}) - "
              "continuing in FTS-only mode (keyword search, exact-hash dedup)",
              file=sys.stderr)
        return None

    enc = _tokenizer.encode(text)
    inputs = {
        "input_ids": np.array([enc.ids], dtype=np.int64),
        "attention_mask": np.array([enc.attention_mask], dtype=np.int64),
    }
    if any(i.name == "token_type_ids" for i in _session.get_inputs()):
        inputs["token_type_ids"] = np.array([enc.type_ids], dtype=np.int64)
    out = _session.run(None, inputs)[0]  # (1, seq, dim)
    mask = np.array(enc.attention_mask, dtype=np.float32)[None, :, None]
    vec = (out * mask).sum(axis=1) / mask.sum(axis=1)  # mean pool
    vec = vec[0].astype(np.float32)
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec
