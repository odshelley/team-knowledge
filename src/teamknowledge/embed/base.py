from __future__ import annotations

from typing import Protocol


class Embedder(Protocol):
    model: str
    dims: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...
