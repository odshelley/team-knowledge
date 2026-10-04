from datetime import date
from pathlib import Path

import pytest

from teamknowledge.cli import main
from teamknowledge.embed.fake import FakeEmbedder
from teamknowledge.githost.local import LocalGitHost
from teamknowledge.graph.sync import Syncer, SyncError
from teamknowledge.model import Entity
from teamknowledge.propose import ProposalInput, Proposer
from teamknowledge.validate import Validator

pytestmark = pytest.mark.neo4j

EXAMPLE_ID = "01J9XK3M8Q7ZV2W1F4N6B5HT9D"


class BrokenEmbedder:
    model = "fake"
    dims = 64

    def embed(self, texts):
        raise ConnectionError("embedder down")


@pytest.fixture
def world(knowledge_repo, graph):
    host = LocalGitHost(Path(knowledge_repo.remote_url()), author="alice")
    e = Entity(type="system", slug="kdb-gateway", name="kdb+ gateway", aliases=["gw"], owner=["bob"],
               description="The q process that fronts the tick databases for the pricing desk.", related=["system/example-system"])
    knowledge_repo.write_entity(e)
    knowledge_repo.commit_files([e.path], "add gateway")
    knowledge_repo.push("main")
    proposer = Proposer(knowledge_repo, Validator(knowledge_repo.schema_dir), host, author="alice", today=lambda: date(2026, 10, 4))
    syncer = Syncer(knowledge_repo, graph, embedder=FakeEmbedder(), host=host, sleep=lambda s: None)
    return knowledge_repo, graph, host, proposer, syncer


def propose_and_merge(proposer, host, **kw) -> str:
    inp = ProposalInput(kind="dead-end", title=kw.pop("title", "Batch queries over 10k symbols time out"),
                        claim=kw.pop("claim", "Selects over more than ten thousand symbols hit the thirty second gateway timeout."),
                        scope=kw.pop("scope", ["system/kdb-gateway"]),
                        evidence=kw.pop("evidence", [{"type": "observation", "note": "Reproduced on 2026-10-04 with 12k symbols."}]),
                        confidence="observed", sections={"Approach": "One select.", "Why it fails": "Timeout."}, **kw)
    r = proposer.propose(inp)
    assert r.errors == [], r.errors
    host.approve(r.branch, "bob")
    proposer.repo.sync_main()
    return r.finding_id


def count(graph, label):
    return graph.run(f"MATCH (n:{label}) RETURN count(n) AS c")[0]["c"]


def test_first_sync_loads_everything_and_is_idempotent(world):
    repo, graph, _host, _proposer, syncer = world
    report = syncer.sync()
    assert report.findings_upserted == 1 and report.entities_upserted == 2 and report.embedded == 1
    assert syncer.meta()["last_sync_commit"] == repo.head_sha()
    assert syncer.meta()["embedding_model"] == "fake" and syncer.meta()["embedding_dims"] == 64
    f = graph.run("MATCH (f:Finding {id: $id}) RETURN f", id=EXAMPLE_ID)[0]["f"]
    assert f["kind"] == "dead-end" and f["status"] == "active" and len(f["embedding"]) == 64 and f["embedding_model"] == "fake"
    assert f["created"] == "2026-10-04" and f["observations"] == ["Written by hand on 2026-10-04 as part of the template."]
    edges = graph.run("MATCH (f:Finding {id: $id})-[r]->(x) RETURN type(r) AS t, coalesce(x.ref, x.username) AS k ORDER BY t", id=EXAMPLE_ID)
    assert edges == [{"t": "AUTHORED_BY", "k": "example.author"}, {"t": "SCOPED_TO", "k": "system/example-system"}]
    rel = graph.run("MATCH (a:Entity {ref: 'system/kdb-gateway'})-[:RELATED_TO]->(b) RETURN b.ref AS ref")
    assert rel == [{"ref": "system/example-system"}]
    assert graph.run("MATCH (e:Entity {ref: 'system/kdb-gateway'})-[:OWNED_BY]->(p) RETURN p.username AS u") == [{"u": "bob"}]
    assert syncer.sync().unchanged is True


def test_incremental_sync_after_merge_sets_edges_and_review_metadata(world):
    repo, graph, host, proposer, syncer = world
    syncer.sync()
    fid = propose_and_merge(proposer, host, scope=["system/kdb-gateway", "environment/uat"],
                            evidence=[{"type": "observation", "note": "Reproduced on 2026-10-04 with 12k symbols."},
                                      {"type": "confluence", "ref": "https://confluence.bank/x/KDB", "note": "limits page"}],
                            new_entities=[{"type": "environment", "slug": "uat", "name": "UAT", "description": "User acceptance testing environment for the desk."}])
    report = syncer.sync()
    assert report.findings_upserted == 1 and report.entities_upserted == 1 and report.review_metadata_set == 1
    f = graph.run("MATCH (f:Finding {id: $id}) RETURN f", id=fid)[0]["f"]
    assert f["mr_url"] == "local://merge-requests/1" and f["approved_by"] == ["bob"] and f["merged_at"]
    assert f["commit"] == repo.head_sha()
    cites = graph.run("MATCH (f:Finding {id: $id})-[c:CITES]->(s:Source) RETURN s.ref AS ref, s.type AS type, c.note AS note", id=fid)
    assert cites == [{"ref": "https://confluence.bank/x/KDB", "type": "confluence", "note": "limits page"}]
    assert count(graph, "Entity") == 3 and count(graph, "Finding") == 2


def test_supersede_edges_and_status(world):
    _repo, graph, host, proposer, syncer = world
    syncer.sync()
    first = propose_and_merge(proposer, host)
    syncer.sync()
    second = propose_and_merge(proposer, host, title="Batch queries over 5k symbols time out", supersedes=[first])
    report = syncer.sync()
    assert report.findings_upserted == 2  # the new one and the edited old one
    assert graph.run("MATCH (n:Finding {id: $n})-[:SUPERSEDES]->(o) RETURN o.id AS id", n=second) == [{"id": first}]
    old = graph.run("MATCH (f:Finding {id: $id}) RETURN f.status AS s, f.superseded_by AS by", id=first)[0]
    assert old == {"s": "superseded", "by": second}


def test_deleted_file_removes_node(world):
    repo, graph, _host, _proposer, syncer = world
    syncer.sync()
    path = repo.root / "findings" / f"{EXAMPLE_ID}-example-dead-end.md"
    repo.git("rm", "-q", str(path.relative_to(repo.root)))
    repo.git("commit", "-q", "-m", "remove example")
    repo.push("main")
    report = syncer.sync()
    assert report.findings_deleted == 1
    assert count(graph, "Finding") == 0


def test_embedding_failure_is_not_fatal_and_embed_missing_recovers(world):
    repo, graph, host, _proposer, syncer = world
    broken = Syncer(repo, graph, embedder=BrokenEmbedder(), host=host, sleep=lambda s: None)
    report = broken.sync()
    assert report.embedding_failures == 1 and report.warnings and report.findings_upserted == 1
    assert graph.run("MATCH (f:Finding) WHERE f.embedding IS NULL RETURN count(f) AS c")[0]["c"] == 1
    assert broken.meta()["last_sync_commit"] == repo.head_sha()
    assert syncer.embed_missing() == 1
    assert graph.run("MATCH (f:Finding) WHERE f.embedding IS NULL RETURN count(f) AS c")[0]["c"] == 0


def test_model_change_requires_full(world):
    repo, graph, host, _proposer, syncer = world
    syncer.sync()
    other = Syncer(repo, graph, embedder=FakeEmbedder(dims=32, model="fake-32"), host=host, sleep=lambda s: None)
    with pytest.raises(SyncError, match="tk sync --full"):
        other.sync()
    report = other.sync(full=True)
    assert report.findings_upserted == 1 and other.meta()["embedding_dims"] == 32
    opts = graph.run("SHOW INDEXES YIELD name, options WHERE name = 'finding_embedding' RETURN options")[0]["options"]
    assert opts["indexConfig"]["vector.dimensions"] == 32


def test_embed_missing_rejects_model_mismatch(world):
    repo, graph, host, _proposer, syncer = world
    syncer.sync()
    other = Syncer(repo, graph, embedder=FakeEmbedder(dims=32, model="fake-32"), host=host, sleep=lambda s: None)
    with pytest.raises(SyncError, match="tk sync --full"):
        other.embed_missing()


def test_validation_error_aborts_without_advancing_meta(world):
    repo, _graph, _host, _proposer, syncer = world
    syncer.sync()
    before = syncer.meta()["last_sync_commit"]
    bad = repo.root / "findings" / "01J9XK3M8Q7ZV2W1F4N6B5HT9Q-bad.md"
    bad.write_text("---\nid: 01J9XK3M8Q7ZV2W1F4N6B5HT9Q\nkind: fact\n---\n")
    repo.commit_files([bad.relative_to(repo.root)], "bad")
    repo.push("main")
    with pytest.raises(SyncError, match="validation errors"):
        syncer.sync()
    assert syncer.meta()["last_sync_commit"] == before


def test_cli_sync(world, monkeypatch, capsys):
    repo, graph, _host, _proposer, _syncer = world
    monkeypatch.setenv("NEO4J_URI", graph.uri)
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", graph.password)
    monkeypatch.setenv("TK_EMBEDDER", "fake")
    monkeypatch.setenv("TK_GITHOST", "local")
    assert main(["sync", "--repo", str(repo.root)]) == 0
    out = capsys.readouterr().out
    assert "findings upserted: 1" in out and "entities upserted: 2" in out
    assert main(["sync", "--repo", str(repo.root)]) == 0
    assert "unchanged" in capsys.readouterr().out
