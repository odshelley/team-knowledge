import json
from datetime import date
from pathlib import Path

import pytest
from mcp.shared.memory import create_connected_server_and_client_session as connect

from teamknowledge.embed.fake import FakeEmbedder
from teamknowledge.githost.local import LocalGitHost
from teamknowledge.graph.client import GraphClient
from teamknowledge.graph.queries import Reader
from teamknowledge.graph.sync import Syncer
from teamknowledge.mcp_server import Services, build_server
from teamknowledge.model import Entity
from teamknowledge.propose import Proposer
from teamknowledge.validate import Validator
from teamknowledge.cli import build_parser

TOOLS = {"propose_finding", "amend_finding", "retract_finding", "findings_for_scope", "search_findings",
         "get_finding", "list_entities", "my_proposals"}


async def call(server, name, args):
    async with connect(server._mcp_server) as client:
        result = await client.call_tool(name, args)
        if result.structuredContent is not None:
            return result.structuredContent
        return json.loads(result.content[0].text)


@pytest.fixture
def writer_services(knowledge_repo):
    e = Entity(type="system", slug="kdb-gateway", name="kdb+ gateway", owner=["bob"],
               description="The q process that fronts the tick databases for the pricing desk.")
    knowledge_repo.write_entity(e)
    knowledge_repo.commit_files([e.path], "gateway")
    knowledge_repo.push("main")
    host = LocalGitHost(Path(knowledge_repo.remote_url()), author="alice")
    proposer = Proposer(knowledge_repo, Validator(knowledge_repo.schema_dir), host, author="alice", today=lambda: date(2026, 10, 4))
    return Services(proposer=proposer, reader=None, host=host, author="alice", wiki_base_url="https://wiki.example/tk")


GOOD = {"kind": "dead-end", "title": "Batch queries over 10k symbols time out",
        "claim": "Selects over more than ten thousand symbols hit the thirty second gateway timeout.",
        "scope": ["system/kdb-gateway"], "evidence": [{"type": "observation", "note": "Reproduced on 2026-10-04 with 12k symbols."}],
        "confidence": "observed", "sections": {"Approach": "One select.", "Why it fails": "Timeout."}}


async def test_lists_all_tools(writer_services):
    server = build_server(writer_services)
    async with connect(server._mcp_server) as client:
        tools = await client.list_tools()
    assert {t.name for t in tools.tools} == TOOLS


async def test_propose_amend_retract_and_my_proposals(writer_services):
    server = build_server(writer_services)
    r = await call(server, "propose_finding", GOOD)
    assert r["merge_request_url"] == "local://merge-requests/1" and r["reviewers"] == ["bob"]
    mine = await call(server, "my_proposals", {})
    assert mine["proposals"][0]["branch"] == r["branch"] and mine["proposals"][0]["state"] == "open"
    bad = await call(server, "propose_finding", {**GOOD, "scope": ["system/kdb-gw"]})
    assert bad["errors"][0]["field"] == "scope[0]"
    writer_services.host.approve(r["branch"], "bob")
    a = await call(server, "amend_finding", {"finding_id": r["finding_id"], "reason": "tighten", "header_changes": {"confidence": "inferred"}})
    assert a["branch"].startswith("amend/")
    x = await call(server, "retract_finding", {"finding_id": r["finding_id"], "reason": "the gateway was upgraded and the limit is gone"})
    assert x["branch"] == f"retract/{r['finding_id']}"


async def test_read_tools_disabled_without_reader(writer_services):
    server = build_server(writer_services)
    r = await call(server, "search_findings", {"query": "x"})
    assert "read tools disabled" in r["error"]


async def test_write_tools_disabled_without_proposer():
    server = build_server(Services(proposer=None, reader=None, host=None, author="x"))
    r = await call(server, "propose_finding", GOOD)
    assert "write tools disabled" in r["error"]


async def test_unreachable_graph_reports_wiki_url():
    client = GraphClient("bolt://127.0.0.1:1", "neo4j", "x")
    services = Services(proposer=None, reader=Reader(client, wiki_base_url="https://wiki.example/tk"), host=None, author="x",
                        wiki_base_url="https://wiki.example/tk")
    r = await call(build_server(services), "findings_for_scope", {"scope": ["system/a"]})
    assert "unreachable" in r["error"] and r["wiki_url"] == "https://wiki.example/tk"


async def test_programming_bug_in_reader_is_not_mislabelled_unreachable():
    class BuggyClient:
        def run(self, *args, **kwargs):
            raise KeyError("boom")

    services = Services(proposer=None, reader=Reader(BuggyClient(), wiki_base_url="https://wiki.example/tk"),
                        host=None, author="x", wiki_base_url="https://wiki.example/tk")
    server = build_server(services)
    async with connect(server._mcp_server) as client:
        result = await client.call_tool("findings_for_scope", {"scope": ["system/a"]})
    assert result.isError
    text = result.content[0].text
    assert "unreachable" not in text
    assert "boom" in text


@pytest.mark.neo4j
async def test_read_tools_against_graph(knowledge_repo, graph):
    e = Entity(type="system", slug="kdb-gateway", name="kdb+ gateway", aliases=["gw"],
               description="The q process that fronts the tick databases for the pricing desk.")
    knowledge_repo.write_entity(e)
    knowledge_repo.commit_files([e.path], "gateway")
    knowledge_repo.push("main")
    Syncer(knowledge_repo, graph, embedder=FakeEmbedder(), sleep=lambda s: None).sync()
    reader = Reader(graph, embedder=FakeEmbedder(), wiki_base_url="https://wiki.example/tk")
    server = build_server(Services(proposer=None, reader=reader, host=None, author="x", wiki_base_url="https://wiki.example/tk"))
    scoped = await call(server, "findings_for_scope", {"scope": ["system/example-system"]})
    assert scoped["findings"][0]["id"] == "01J9XK3M8Q7ZV2W1F4N6B5HT9D"
    found = await call(server, "search_findings", {"query": "template validates"})
    assert found["findings"][0]["id"] == "01J9XK3M8Q7ZV2W1F4N6B5HT9D" and found["warnings"] == []
    one = await call(server, "get_finding", {"finding_id": "01J9XK3M8Q7ZV2W1F4N6B5HT9D"})
    assert one["sections"]["Approach"].startswith("Keep an empty")
    missing = await call(server, "get_finding", {"finding_id": "01J9XK3M8Q7ZV2W1F4N6B5HT99"})
    assert "not found" in missing["error"]
    ents = await call(server, "list_entities", {"query": "gw"})
    assert ents["entities"][0]["ref"] == "system/kdb-gateway"


def test_serve_mcp_subcommand_exists():
    args = build_parser().parse_args(["serve-mcp"])
    assert args.command == "serve-mcp"
