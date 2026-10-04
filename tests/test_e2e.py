"""The whole loop through the CLI: init, propose, approve, sync, query, render."""

import os
import subprocess
import sys
from datetime import date

import pytest

from teamknowledge.embed.fake import FakeEmbedder
from teamknowledge.githost.local import LocalGitHost
from teamknowledge.graph.queries import Reader
from teamknowledge.model import Entity
from teamknowledge.propose import ProposalInput, Proposer
from teamknowledge.repo import KnowledgeRepo
from teamknowledge.validate import Validator

pytestmark = pytest.mark.neo4j


def tk(*args, env):
    result = subprocess.run([sys.executable, "-m", "teamknowledge.cli", *args], capture_output=True, text=True, env=env, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_whole_loop(tmp_path, graph):
    env = {**os.environ, "TK_GITHOST": "local", "TK_AUTHOR": "alice", "TK_EMBEDDER": "fake",
           "NEO4J_URI": graph.uri, "NEO4J_USERNAME": "neo4j", "NEO4J_PASSWORD": graph.password, "NEO4J_DATABASE": "neo4j"}
    clone, remote = tmp_path / "kr", tmp_path / "kr.git"
    tk("init", str(clone), "--local-remote", str(remote), env=env)
    repo = KnowledgeRepo(clone)

    e = Entity(type="system", slug="kdb-gateway", name="kdb+ gateway", aliases=["gw"], owner=["bob"],
               description="The q process that fronts the tick databases for the pricing desk.")
    repo.write_entity(e)
    repo.commit_files([e.path], "gateway")
    repo.push("main")
    tk("validate", "--repo", str(clone), env=env)

    host = LocalGitHost(remote, author="alice")
    proposer = Proposer(repo, Validator(repo.schema_dir), host, author="alice", today=lambda: date(2026, 10, 4))
    r = proposer.propose(ProposalInput(
        kind="dead-end", title="Batch queries over 10k symbols time out",
        claim="Selects over more than ten thousand symbols hit the thirty second gateway timeout.",
        scope=["system/kdb-gateway"], evidence=[{"type": "observation", "note": "Reproduced on 2026-10-04 with 12k symbols."}],
        confidence="observed", sections={"Approach": "One select.", "Why it fails": "Timeout."}))
    assert r.merge_request_url and not r.errors

    assert "finding/" in tk("review", "list", "--repo", str(clone), env=env)
    tk("review", "approve", r.branch, "--as", "bob", "--repo", str(clone), env=env)
    repo.sync_main()

    out = tk("sync", "--repo", str(clone), env=env)
    assert "findings upserted: 2" in out and "review metadata set: 1" in out

    reader = Reader(graph, embedder=FakeEmbedder())
    found = reader.findings_for_scope(["system/kdb-gateway"])
    assert [f.id for f in found] == [r.finding_id]
    assert reader.search("gateway timeout").findings[0].id == r.finding_id
    detail = reader.get_finding(r.finding_id)
    assert detail["approved_by"] == ["bob"] and detail["mr_url"] == "local://merge-requests/1"

    tk("render", "--repo", str(clone), "--out", str(tmp_path / "site"), env=env)
    page = (tmp_path / "site" / "findings" / f"{r.finding_id}.html").read_text()
    assert "Batch queries over 10k symbols time out" in page

    assert "unchanged" in tk("sync", "--repo", str(clone), env=env)
