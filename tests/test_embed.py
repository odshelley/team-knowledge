import json
import math
import os
from typing import ClassVar

import pytest

from teamknowledge.embed import make_embedder
from teamknowledge.embed.bedrock import BedrockEmbedder
from teamknowledge.embed.fake import FakeEmbedder
from teamknowledge.embed.openai import OpenAIEmbedder


def test_fake_is_deterministic_and_unit_length():
    e = FakeEmbedder()
    a, b = e.embed(["hello", "hello"])
    assert a == b and len(a) == 64 and e.dims == 64 and e.model == "fake"
    assert abs(math.sqrt(sum(x * x for x in a)) - 1.0) < 1e-6
    assert e.embed(["hello"])[0] != e.embed(["goodbye"])[0]


def test_make_embedder():
    assert make_embedder("none") is None and make_embedder(None) is None and make_embedder("") is None
    assert isinstance(make_embedder("fake"), FakeEmbedder)
    with pytest.raises(ValueError):
        make_embedder("mystery")


class FakeOpenAI:
    class embeddings:
        @staticmethod
        def create(model, input):
            class R:
                data: ClassVar = [type("D", (), {"embedding": [float(i)] * 3})() for i in range(len(input))]
            return R()


def test_openai_embedder_uses_client():
    e = OpenAIEmbedder(dims=3, client=FakeOpenAI())
    assert e.embed(["a", "b"]) == [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]
    assert e.model == "text-embedding-3-small"


class FakeBedrock:
    def __init__(self):
        self.calls = []

    def invoke_model(self, modelId, body, contentType, accept):
        self.calls.append(json.loads(body))
        return {"body": type("B", (), {"read": lambda self: json.dumps({"embedding": [0.5, 0.5]}).encode()})()}


def test_bedrock_embedder_calls_titan():
    client = FakeBedrock()
    e = BedrockEmbedder(dims=2, client=client)
    assert e.embed(["x", "y"]) == [[0.5, 0.5], [0.5, 0.5]]
    assert client.calls[0] == {"inputText": "x", "dimensions": 2, "normalize": True}
    assert e.model == "amazon.titan-embed-text-v2:0"


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
def test_live_openai():
    v = OpenAIEmbedder().embed(["smoke"])[0]
    assert len(v) == 1536


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("TK_LIVE_BEDROCK"), reason="TK_LIVE_BEDROCK not set")
def test_live_bedrock():
    v = BedrockEmbedder().embed(["smoke"])[0]
    assert len(v) == 1024
