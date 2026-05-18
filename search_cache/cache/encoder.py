"""Lazy-loaded SentenceTransformer encoder. Does not block the MCP handshake."""
from __future__ import annotations

_model = None


def get_encoder():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model


def encode(text: str) -> list[float]:
    """Encode text to a normalized 384-dim float32 embedding."""
    return get_encoder().encode(text, normalize_embeddings=True).tolist()
