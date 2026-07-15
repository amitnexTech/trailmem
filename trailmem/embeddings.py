"""Lazy ONNX embedding. Model absent/disabled → embed() returns None (FTS-only mode).

No hash-embedding pseudo-vectors — degrade loudly, never fake similarity.
"""

from pathlib import Path

from .config import MODELS_DIR, load_config

_session = None
_tokenizer = None


def _model_dir() -> Path:
    return MODELS_DIR / load_config()["embedding"]["model"]


def available() -> bool:
    cfg = load_config()
    if not cfg["embedding"]["enabled"]:
        return False
    d = _model_dir()
    return (d / "model.onnx").exists() and (d / "tokenizer.json").exists()


def embed(text: str):
    """Return a normalized float32 vector, or None when embeddings are unavailable.

    numpy/onnxruntime/tokenizers are imported here, not at module top —
    FTS-only mode must work without the embedding deps installed.
    """
    global _session, _tokenizer
    if not available():
        return None
    import numpy as np
    import onnxruntime
    from tokenizers import Tokenizer

    if _session is None:
        d = _model_dir()
        _session = onnxruntime.InferenceSession(str(d / "model.onnx"))
        _tokenizer = Tokenizer.from_file(str(d / "tokenizer.json"))
        _tokenizer.enable_truncation(max_length=512)

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
