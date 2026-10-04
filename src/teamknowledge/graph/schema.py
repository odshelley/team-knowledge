"""Constraints and indexes. Everything here exists in Neo4j Community 5.13+."""

from __future__ import annotations

from .client import GraphClient

CONSTRAINTS_AND_INDEXES = [
    "CREATE CONSTRAINT finding_id  IF NOT EXISTS FOR (f:Finding) REQUIRE f.id IS UNIQUE",
    "CREATE CONSTRAINT entity_ref  IF NOT EXISTS FOR (e:Entity)  REQUIRE e.ref IS UNIQUE",
    "CREATE CONSTRAINT source_ref  IF NOT EXISTS FOR (s:Source)  REQUIRE s.ref IS UNIQUE",
    "CREATE CONSTRAINT person_name IF NOT EXISTS FOR (p:Person)  REQUIRE p.username IS UNIQUE",
    "CREATE CONSTRAINT meta_key    IF NOT EXISTS FOR (m:Meta)    REQUIRE m.key IS UNIQUE",
    "CREATE INDEX finding_status IF NOT EXISTS FOR (f:Finding) ON (f.status)",
    "CREATE INDEX finding_kind   IF NOT EXISTS FOR (f:Finding) ON (f.kind)",
    "CREATE INDEX entity_type    IF NOT EXISTS FOR (e:Entity)  ON (e.type)",
    "CREATE FULLTEXT INDEX finding_text IF NOT EXISTS FOR (f:Finding) ON EACH [f.title, f.claim, f.body]",
    "CREATE FULLTEXT INDEX entity_text  IF NOT EXISTS FOR (e:Entity)  ON EACH [e.name, e.aliases_text, e.description]",
]


def vector_index_statement(dims: int) -> str:
    return (
        "CREATE VECTOR INDEX finding_embedding IF NOT EXISTS FOR (f:Finding) ON (f.embedding) "
        f"OPTIONS {{ indexConfig: {{ `vector.dimensions`: {int(dims)}, `vector.similarity_function`: 'cosine' }} }}"
    )


def init_schema(client: GraphClient, dims: int | None) -> None:
    for statement in CONSTRAINTS_AND_INDEXES:
        client.run(statement)
    if dims:
        client.run(vector_index_statement(dims))
    client.run("CALL db.awaitIndexes()")


def drop_vector_index(client: GraphClient) -> None:
    client.run("DROP INDEX finding_embedding IF EXISTS")


def wipe(client: GraphClient) -> None:
    client.run_autocommit("MATCH (n) CALL { WITH n DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS")
