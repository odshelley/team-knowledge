"""Environment-driven settings and factories shared by the CLI and the MCP server."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Settings:
    repo: Path | None
    githost: str
    gitlab_mode: str
    gitlab_url: str
    gitlab_token: str | None
    author: str
    neo4j_uri: str | None
    neo4j_username: str
    neo4j_password: str | None
    neo4j_database: str
    embedder: str

    @classmethod
    def from_env(cls, env=os.environ) -> "Settings":
        repo = env.get("TK_REPO")
        return cls(
            repo=Path(repo) if repo else None,
            githost=env.get("TK_GITHOST", "local"),
            gitlab_mode=env.get("TK_GITLAB_MODE", "api"),
            gitlab_url=env.get("GITLAB_URL", "https://gitlab.com"),
            gitlab_token=env.get("GITLAB_TOKEN"),
            author=env.get("TK_AUTHOR", "tk"),
            neo4j_uri=env.get("NEO4J_URI"),
            neo4j_username=env.get("NEO4J_USERNAME", "neo4j"),
            neo4j_password=env.get("NEO4J_PASSWORD"),
            neo4j_database=env.get("NEO4J_DATABASE", "neo4j"),
            embedder=env.get("TK_EMBEDDER", "none"),
        )


def make_graph_client(s: Settings):
    from .graph.client import GraphClient

    if not s.neo4j_uri or s.neo4j_password is None:
        raise SystemExit("NEO4J_URI and NEO4J_PASSWORD must be set")
    return GraphClient(s.neo4j_uri, s.neo4j_username, s.neo4j_password, s.neo4j_database)


def make_embedder_from(s: Settings):
    from .embed import make_embedder

    return make_embedder(s.embedder)
