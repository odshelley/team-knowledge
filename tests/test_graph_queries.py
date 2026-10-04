from datetime import date

import pytest

from teamknowledge.embed.fake import FakeEmbedder
from teamknowledge.graph.queries import Reader, lucene_escape, rrf
from teamknowledge.graph.sync import Syncer
from teamknowledge.model import Entity, Evidence, Finding


def test_lucene_escape():
    assert lucene_escape("kdb+ gateway (uat)") == "kdb\\+ OR gateway OR \\(uat\\)"
    assert lucene_escape("timeout AND select") == "timeout OR select"
    assert lucene_escape("") == ""


def test_rrf():
    scores = rrf([["a", "b"], ["b", "c"]], k=60)
    assert scores["b"] > scores["a"] > 0 and scores["a"] == pytest.approx(1 / 61)
    assert scores["b"] == pytest.approx(1 / 62 + 1 / 61)


FIDS = [f"01J9XK3M8Q7ZV2W1F4N6B5HT{i:02d}"[:26] for i in range(10, 16)]


def mk(i, kind, title, claim, scope, status="active", **kw):
    sections = {"dead-end": {"Approach": "A.", "Why it fails": "B."}, "fact": {"Detail": "D.", "How to check": "H."},
                "how-to": {"Steps": "S.", "Verify": "V."}}[kind]
    return Finding(id=FIDS[i], kind=kind, title=title, claim=claim, scope=scope,
                   evidence=[Evidence("observation", note="Observed during the 2026-10 investigation.")], confidence="observed",
                   status=status, author="alice", created=date(2026, 10, 1 + i), sections=sections, **kw)


@pytest.fixture
def reader(knowledge_repo, graph):
    ents = [Entity(type="system", slug="kdb-gateway", name="kdb+ gateway", aliases=["gw"], description="Fronts the tick databases for the pricing desk.", related=["project/gamma"]),
            Entity(type="project", slug="gamma", name="Gamma", description="The gamma pricing migration project for rates."),
            Entity(type="environment", slug="uat", name="UAT", description="User acceptance testing environment for the desk.")]
    paths = [knowledge_repo.write_entity(e) for e in ents]
    fs = [
        mk(0, "dead-end", "Batch selects over 10k symbols time out on the gateway", "Large selects exceed the thirty second gateway timeout.", ["system/kdb-gateway", "environment/uat"]),
        mk(1, "fact", "Gamma nightly batch starts at 02:00 London", "The gamma nightly batch is scheduled at two in the morning London time.", ["project/gamma"]),
        mk(2, "how-to", "Chunk symbol lists before querying the gateway", "Split symbol lists into chunks of five thousand and query concurrently.", ["system/kdb-gateway"]),
        mk(3, "fact", "UAT database is refreshed every Sunday", "The UAT database is rebuilt from production every Sunday night.", ["environment/uat"]),
        mk(4, "dead-end", "Retracted claim about gateway caching", "The gateway does not cache query results between calls.", ["system/kdb-gateway"], status="retracted", retracted_reason="Caching was added in version 4.2."),
    ]
    paths += [knowledge_repo.write_finding(f) for f in fs]
    knowledge_repo.commit_files(paths, "seed")
    knowledge_repo.push("main")
    Syncer(knowledge_repo, graph, embedder=FakeEmbedder(), sleep=lambda s: None).sync()
    return Reader(graph, embedder=FakeEmbedder(), wiki_base_url="https://wiki.example/tk/")


@pytest.mark.neo4j
def test_findings_for_scope_ranks_direct_before_related(reader):
    out = reader.findings_for_scope(["system/kdb-gateway"])
    ids = [o.id for o in out]
    assert ids[:2] == [FIDS[2], FIDS[0]]           # direct, newest first
    assert FIDS[1] in ids and ids.index(FIDS[1]) > 1  # gamma via RELATED_TO
    assert FIDS[4] not in ids                      # retracted excluded
    assert out[0].wiki_url == f"https://wiki.example/tk/findings/{FIDS[2]}.html"
    assert out[0].matched_scope == ["system/kdb-gateway"]


@pytest.mark.neo4j
def test_findings_for_scope_without_related_and_with_kinds(reader):
    assert [o.id for o in reader.findings_for_scope(["system/kdb-gateway"], include_related=False)] == [FIDS[2], FIDS[0]]
    assert [o.id for o in reader.findings_for_scope(["system/kdb-gateway", "environment/uat"], kinds=["fact"])] == [FIDS[3], FIDS[1]]
    assert [o.id for o in reader.findings_for_scope(["system/kdb-gateway", "environment/uat"], kinds=["fact"], include_related=False)] == [FIDS[3]]
    assert reader.findings_for_scope(["system/nothing"]) == []


@pytest.mark.neo4j
def test_search_fulltext_and_fused(reader, graph):
    r = reader.search("gateway timeout")
    assert r.warnings == []
    assert FIDS[0] in [f.id for f in r.findings[:2]] and r.findings[0].score is not None
    assert FIDS[4] not in [f.id for f in r.findings]
    r2 = Reader(graph, embedder=None).search("gateway timeout")
    assert r2.findings[0].id == FIDS[0] and "full-text only" in r2.warnings[0]


@pytest.mark.neo4j
def test_search_scope_boost(reader):
    plain = reader.search("Sunday gateway")
    boosted = reader.search("Sunday gateway", scope=["environment/uat"])
    assert FIDS[3] in [f.id for f in plain.findings] and FIDS[3] in [f.id for f in boosted.findings]
    uat_plain = next(f.score for f in plain.findings if f.id == FIDS[3])
    uat_boost = next(f.score for f in boosted.findings if f.id == FIDS[3])
    assert uat_boost == pytest.approx(uat_plain * 1.5)


@pytest.mark.neo4j
def test_search_kinds_filter_and_empty(reader):
    assert all(f.kind == "fact" for f in reader.search("gateway", kinds=["fact"]).findings)


@pytest.mark.neo4j
def test_search_nonsense_query_returns_nothing_with_fake_embedder(reader):
    r = reader.search("zzzzqqq")
    assert r.findings == []
    assert r.warnings == []


@pytest.mark.neo4j
def test_search_pure_semantic_recall(knowledge_repo, graph, reader):
    class ConstantEmbedder:
        model = "const"
        dims = 64

        def embed(self, texts: list[str]) -> list[list[float]]:
            return [[1.0] + [0.0] * 63 for _ in texts]

    Syncer(knowledge_repo, graph, embedder=ConstantEmbedder(), sleep=lambda s: None).sync(full=True)
    r = Reader(graph, embedder=ConstantEmbedder()).search("zzzzqqq")
    ids = {f.id for f in r.findings}
    # Every active finding in the repo gets re-embedded to the identical constant vector by the
    # full resync, so this also includes the template's own example finding (also active) -
    # not just the five seeded for this fixture.
    assert {FIDS[0], FIDS[1], FIDS[2], FIDS[3]} <= ids
    assert FIDS[4] not in ids


@pytest.mark.neo4j
def test_get_finding(reader):
    d = reader.get_finding(FIDS[0])
    assert d["sections"] == {"Approach": "A.", "Why it fails": "B."}
    assert d["scope"] == ["environment/uat", "system/kdb-gateway"]
    assert d["evidence"] == [{"type": "observation", "note": "Observed during the 2026-10 investigation."}]
    assert d["supersedes"] == [] and d["superseded_by"] is None and d["contradicts"] == []
    assert d["wiki_url"].endswith(f"{FIDS[0]}.html")
    assert reader.get_finding("01J9XK3M8Q7ZV2W1F4N6B5HT99") is None


@pytest.mark.neo4j
def test_list_entities(reader):
    by_alias = reader.list_entities(query="gw")
    assert by_alias[0]["ref"] == "system/kdb-gateway" and by_alias[0]["active_findings"] == 2
    envs = reader.list_entities(type="environment")
    assert [e["ref"] for e in envs] == ["environment/uat"]
    assert len(reader.list_entities()) == 4  # includes the template example
