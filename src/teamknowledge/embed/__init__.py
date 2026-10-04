from __future__ import annotations

from .base import Embedder


def make_embedder(name: str | None) -> Embedder | None:
    name = (name or "none").lower()
    if name == "none":
        return None
    if name == "fake":
        from .fake import FakeEmbedder

        return FakeEmbedder()
    if name == "openai":
        from .openai import OpenAIEmbedder

        return OpenAIEmbedder()
    if name == "bedrock":
        from .bedrock import BedrockEmbedder

        return BedrockEmbedder()
    raise ValueError(f"unknown embedder {name!r}; use none, fake, openai, or bedrock")


__all__ = ["Embedder", "make_embedder"]
