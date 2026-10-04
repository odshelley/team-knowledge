from __future__ import annotations

import json
import os


class BedrockEmbedder:
    def __init__(self, model: str = "amazon.titan-embed-text-v2:0", dims: int = 1024, region: str | None = None, client=None):
        self.model = model
        self.dims = dims
        if client is None:
            import boto3  # optional extra

            client = boto3.client("bedrock-runtime", region_name=region or os.environ.get("AWS_REGION"))
        self.client = client

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            body = json.dumps({"inputText": text, "dimensions": self.dims, "normalize": True})
            response = self.client.invoke_model(modelId=self.model, body=body, contentType="application/json", accept="application/json")
            out.append([float(x) for x in json.loads(response["body"].read())["embedding"]])
        return out
