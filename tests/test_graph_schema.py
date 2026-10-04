import pytest

from teamknowledge.cli import main
from teamknowledge.graph.schema import drop_vector_index, init_schema, vector_index_statement, wipe
from teamknowledge.settings import Settings


def index_names(client):
    return {r["name"] for r in client.run("SHOW INDEXES YIELD name RETURN name")}


@pytest.mark.neo4j
def test_init_schema_is_idempotent(graph):
    before = index_names(graph)
    init_schema(graph, dims=64)
    assert index_names(graph) == before
    assert {"finding_text", "entity_text", "finding_embedding", "finding_status", "finding_kind", "entity_type"} <= before
    constraints = {r["name"] for r in graph.run("SHOW CONSTRAINTS YIELD name RETURN name")}
    assert {"finding_id", "entity_ref", "source_ref", "person_name", "meta_key"} <= constraints


@pytest.mark.neo4j
def test_vector_index_optional(neo4j_client):
    wipe(neo4j_client)
    drop_vector_index(neo4j_client)
    init_schema(neo4j_client, dims=None)
    assert "finding_embedding" not in index_names(neo4j_client)
    assert "`vector.dimensions`: 1024" in vector_index_statement(1024)


@pytest.mark.neo4j
def test_wipe_removes_everything(graph):
    graph.run("CREATE (:Finding {id: 'x'})-[:SCOPED_TO]->(:Entity {ref: 'system/a'})")
    wipe(graph)
    assert graph.run("MATCH (n) RETURN count(n) AS c")[0]["c"] == 0


@pytest.mark.neo4j
def test_cli_graph_init(neo4j_client, monkeypatch, capsys):
    monkeypatch.setenv("NEO4J_URI", neo4j_client.uri)
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", neo4j_client.password)
    monkeypatch.setenv("TK_EMBEDDER", "fake")
    assert main(["graph", "init"]) == 0
    assert "vector index: 64 dims" in capsys.readouterr().out


def test_settings_from_env():
    s = Settings.from_env({"TK_REPO": "/x", "NEO4J_URI": "bolt://h:7687", "NEO4J_PASSWORD": "p", "TK_EMBEDDER": "fake"})
    assert str(s.repo) == "/x" and s.neo4j_username == "neo4j" and s.neo4j_database == "neo4j"
    assert s.githost == "local" and s.gitlab_mode == "api" and s.embedder == "fake"
