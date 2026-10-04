"""The team-knowledge MCP server. Runs locally over stdio inside each person's Claude Code.

Write tools open merge requests through the git host. Read tools query Neo4j, which holds
accepted findings only. The server never writes to Neo4j.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mcp.server.fastmcp import FastMCP

from .githost.base import GitHost
from .graph.queries import Reader
from .propose import ProposalInput, Proposer
from .settings import Settings, make_embedder_from, make_graph_client, make_host

INSTRUCTIONS = (
    "Team Knowledge holds structured, reviewed findings scoped by entities (project, system, environment, desk, tool). "
    "Use findings_for_scope and search_findings to look things up; use list_entities to resolve scope refs before proposing. "
    "propose_finding opens a merge request that a colleague reviews; nothing is visible to others until it is merged."
)


@dataclass
class Services:
    proposer: Proposer | None
    reader: Reader | None
    host: GitHost | None
    author: str
    wiki_base_url: str = ""


def build_services(s: Settings) -> Services:
    from .repo import KnowledgeRepo
    from .validate import Validator

    repo = KnowledgeRepo(s.repo) if s.repo else None
    wiki = (repo.config.get("wiki", {}) or {}).get("base_url", "") if repo else ""
    host = make_host(s, repo) if repo else None
    proposer = Proposer(repo, Validator(repo.schema_dir), host, author=s.author) if repo and host else None
    reader = Reader(make_graph_client(s), make_embedder_from(s), wiki) if s.neo4j_uri else None
    return Services(proposer=proposer, reader=reader, host=host, author=s.author, wiki_base_url=wiki)


def build_server(s: Services) -> FastMCP:
    mcp = FastMCP("team-knowledge", instructions=INSTRUCTIONS)

    def need_writer() -> dict | None:
        return None if s.proposer else {"error": "write tools disabled: TK_REPO not set"}

    def read(fn) -> dict:
        if s.reader is None:
            return {"error": "read tools disabled: NEO4J_URI not set", "wiki_url": s.wiki_base_url}
        try:
            return fn()
        except Exception as exc:  # driver errors, network errors
            return {"error": f"knowledge graph unreachable: {type(exc).__name__}: {exc}", "wiki_url": s.wiki_base_url}

    @mcp.tool()
    def propose_finding(kind: str, title: str, claim: str, scope: list[str], evidence: list[dict[str, Any]],
                        confidence: str, sections: dict[str, str], applies_when: str | None = None,
                        supersedes: list[str] | None = None, review_after: str | None = None,
                        new_entities: list[dict[str, Any]] | None = None) -> dict:
        """Propose a new finding as a merge request.

        kind: dead-end | caveat | how-to | decision | fact. scope: entity refs like system/kdb-gateway (resolve with
        list_entities; add missing ones in new_entities with type, slug, name, description). evidence: at least one item,
        {"type": "observation", "note": ...} or {"type": "confluence|gitlab|runbook|url", "ref": ..., "note": ...}.
        confidence: observed | inferred | reported. sections: the kind's required headings mapped to markdown text.
        Returns finding_id, branch, merge_request_url, reviewers, or {"errors": [...]} with field, message, suggestion.
        """
        if err := need_writer():
            return err
        return s.proposer.propose(ProposalInput(kind=kind, title=title, claim=claim, scope=scope, evidence=evidence,
                                                confidence=confidence, sections=sections, applies_when=applies_when,
                                                supersedes=supersedes or [], review_after=review_after,
                                                new_entities=new_entities or [])).to_dict()

    @mcp.tool()
    def amend_finding(finding_id: str, reason: str, header_changes: dict[str, Any] | None = None,
                      sections: dict[str, str] | None = None) -> dict:
        """Open a merge request correcting an accepted finding without changing its claim's identity.

        header_changes may contain title, claim, scope, applies_when, evidence, confidence, contradicts, review_after.
        """
        if err := need_writer():
            return err
        return s.proposer.amend(finding_id, reason, header_changes, sections).to_dict()

    @mcp.tool()
    def retract_finding(finding_id: str, reason: str) -> dict:
        """Open a merge request marking a finding retracted, with the reason."""
        if err := need_writer():
            return err
        return s.proposer.retract(finding_id, reason).to_dict()

    @mcp.tool()
    def findings_for_scope(scope: list[str], kinds: list[str] | None = None, include_related: bool = True,
                           limit: int = 50) -> dict:
        """Active findings scoped to any of the given entity refs, expanded one hop over related entities."""
        return read(lambda: {"findings": [f.to_dict() for f in s.reader.findings_for_scope(scope, kinds or [], include_related, limit)]})

    @mcp.tool()
    def search_findings(query: str, scope: list[str] | None = None, kinds: list[str] | None = None, limit: int = 10) -> dict:
        """Hybrid full-text and semantic search over active findings; scope refs boost matches."""
        return read(lambda: s.reader.search(query, scope or [], kinds or [], limit).to_dict())

    @mcp.tool()
    def get_finding(finding_id: str) -> dict:
        """The full finding: sections, evidence, review metadata, supersedes and contradicts links."""
        def go():
            d = s.reader.get_finding(finding_id)
            return d if d is not None else {"error": f"finding {finding_id} not found"}
        return read(go)

    @mcp.tool()
    def list_entities(type: str | None = None, query: str | None = None, limit: int = 20) -> dict:
        """Resolve the entity vocabulary by type and fuzzy name or alias; use before proposing."""
        return read(lambda: {"entities": s.reader.list_entities(type, query, limit)})

    @mcp.tool()
    def my_proposals() -> dict:
        """The caller's open merge requests on the knowledge repo."""
        if s.host is None:
            return {"error": "git host not configured"}
        try:
            mrs = s.host.list_open_by(s.author)
        except Exception as exc:
            return {"error": f"git host unreachable: {exc}"}
        return {"proposals": [{"iid": m.iid, "branch": m.branch, "url": m.url, "state": m.state} for m in mrs]}

    return mcp


def main() -> None:
    build_server(build_services(Settings.from_env())).run()
