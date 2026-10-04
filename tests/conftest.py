from pathlib import Path

import pytest

from teamknowledge.graph.client import GraphClient
from teamknowledge.graph.schema import drop_vector_index, init_schema, wipe
from teamknowledge.repo import KnowledgeRepo, init_knowledge_repo
from teamknowledge.validate import Validator

NEO4J_IMAGE = "neo4j:5.26-community"


@pytest.fixture
def knowledge_repo(tmp_path: Path) -> KnowledgeRepo:
    return init_knowledge_repo(tmp_path / "clone", local_remote=tmp_path / "remote.git", author="alice")


@pytest.fixture
def validator(knowledge_repo: KnowledgeRepo) -> Validator:
    return Validator(knowledge_repo.schema_dir)


@pytest.fixture(scope="session")
def neo4j_client():
    try:
        from testcontainers.community.neo4j import Neo4jContainer
    except ImportError:
        pytest.skip("testcontainers not installed")
    try:
        container = Neo4jContainer(NEO4J_IMAGE)
        container.start()
    except Exception as exc:  # Docker missing or daemon down
        pytest.skip(f"Docker not available: {exc}")
    client = GraphClient(container.get_connection_url(), "neo4j", container.password, "neo4j")
    yield client
    client.close()
    container.stop()


@pytest.fixture
def graph(neo4j_client):
    wipe(neo4j_client)
    drop_vector_index(neo4j_client)
    init_schema(neo4j_client, dims=64)
    return neo4j_client
