"""Deterministic hash-based vectors for tests. Semantically meaningless, structurally valid."""

from __future__ import annotations

import hashlib
import math
import random


class FakeEmbedder:
    def __init__(self, dims: int = 64, model: str = "fake"):
        self.dims = dims
        self.model = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")
            rng = random.Random(seed)
            v = [rng.uniform(-1, 1) for _ in range(self.dims)]
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out
