"""Deterministic stand-in for an embedding model (tests and demo mode only)."""

import hashlib
import math

from app.services.textutil import tokenize

FAKE_EMBEDDING_MODEL = "fake-embedding-256"
FAKE_EMBEDDING_DIMENSION = 256


def fake_embedding(text: str) -> list[float]:
    """Hashed bag-of-words vector.

    Each token is hashed (SHA-256, so the result is identical in every
    process, unlike Python's built-in ``hash``) to one of 256 buckets, the
    bucket counts are L2-normalised, and texts sharing words therefore get a
    cosine similarity above zero. Text without tokens gives the zero vector.
    """
    vector = [0.0] * FAKE_EMBEDDING_DIMENSION
    for token in tokenize(text):
        digest = hashlib.sha256(token.encode()).digest()
        vector[int.from_bytes(digest[:4], "big") % FAKE_EMBEDDING_DIMENSION] += 1.0
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector] if norm else vector
