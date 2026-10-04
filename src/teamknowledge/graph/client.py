"""Thin wrapper over the neo4j driver."""

from __future__ import annotations

from neo4j import GraphDatabase


class GraphClient:
    def __init__(self, uri: str, username: str, password: str, database: str = "neo4j"):
        self.driver = GraphDatabase.driver(uri, auth=(username, password))
        self.database = database
        self.password = password
        self.uri = uri

    def run(self, query: str, **params) -> list[dict]:
        records, _, _ = self.driver.execute_query(query, params, database_=self.database)
        return [r.data() for r in records]

    def run_autocommit(self, query: str, **params) -> list[dict]:
        with self.driver.session(database=self.database) as session:
            return [r.data() for r in session.run(query, params)]

    def close(self) -> None:
        self.driver.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
