from __future__ import annotations


class OpenAIEmbedder:
    def __init__(self, model: str = "text-embedding-3-small", dims: int = 1536, client=None):
        self.model = model
        self.dims = dims
        if client is None:
            from openai import OpenAI  # optional extra

            client = OpenAI()
        self.client = client

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self.client.embeddings.create(model=self.model, input=texts)
        return [list(d.embedding) for d in response.data]
