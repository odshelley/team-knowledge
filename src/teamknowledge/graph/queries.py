"""Read queries over the graph: scope lookup, hybrid search, get, list entities."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field

from ..embed.base import Embedder
from .client import GraphClient

_LUCENE_SPECIAL = set('+-&|!(){}[]^"~*?:\\/')
RRF_K = 60
SCOPE_BOOST = 1.5
VECTOR_MIN_SCORE = 0.7  # Neo4j cosine vector score is in [0, 1]; 0.7 corresponds to cosine 0.4


def lucene_escape(text: str) -> str:
    terms = []
    for token in text.split():
        escaped = "".join(("\\" + c) if c in _LUCENE_SPECIAL else c for c in token)
        if escaped and escaped.upper() not in ("AND", "OR", "NOT"):
            terms.append(escaped)
    return " OR ".join(terms)


def rrf(ranked_lists: list[list[str]], k: int = RRF_K) -> dict[str, float]:
    scores: dict[str, float] = defaultdict(float)
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked, start=1):
            scores[item] += 1.0 / (k + rank)
    return dict(scores)


@dataclass
class FindingSummary:
    id: str
    kind: str
    title: str
    claim: str
    scope: list[str]
    confidence: str
    status: str
    author: str
    created: str
    wiki_url: str
    score: float | None = None
    matched_scope: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SearchResult:
    findings: list[FindingSummary]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"findings": [f.to_dict() for f in self.findings], "warnings": self.warnings}


SCOPE_QUERY = """
UNWIND $refs AS ref
MATCH (e:Entity {ref: ref})
OPTIONAL MATCH (e)-[:RELATED_TO]-(r:Entity)
WITH collect(DISTINCT e) AS direct, CASE WHEN $include_related THEN collect(DISTINCT r) ELSE [] END AS rel
WITH direct, [x IN rel WHERE x IS NOT NULL AND NOT x IN direct] AS related
UNWIND direct + related AS ent
MATCH (f:Finding {status: 'active'})-[:SCOPED_TO]->(ent)
WHERE size($kinds) = 0 OR f.kind IN $kinds
WITH f, sum(CASE WHEN ent IN direct THEN 1 ELSE 0 END) AS direct_hits,
     count(DISTINCT ent) AS hits, collect(DISTINCT ent.ref) AS matched
ORDER BY direct_hits DESC, hits DESC, f.created DESC
LIMIT $limit
MATCH (f)-[:SCOPED_TO]->(s:Entity)
RETURN f, matched, collect(s.ref) AS scope
"""

FULLTEXT_QUERY = """
CALL db.index.fulltext.queryNodes('finding_text', $q) YIELD node, score
WHERE node.status = 'active' AND (size($kinds) = 0 OR node.kind IN $kinds)
RETURN node.id AS id, score ORDER BY score DESC LIMIT $k
"""

VECTOR_QUERY = """
CALL db.index.vector.queryNodes('finding_embedding', $k, $vec) YIELD node, score
WHERE node.status = 'active' AND score >= $min_score AND (size($kinds) = 0 OR node.kind IN $kinds)
RETURN node.id AS id, score
"""

HYDRATE_QUERY = """
UNWIND $ids AS id
MATCH (f:Finding {id: id})
OPTIONAL MATCH (f)-[:SCOPED_TO]->(e:Entity)
RETURN f, collect(e.ref) AS scope
"""

GET_QUERY = """
MATCH (f:Finding {id: $id})
OPTIONAL MATCH (f)-[:SCOPED_TO]->(e:Entity)
OPTIONAL MATCH (f)-[c:CITES]->(s:Source)
OPTIONAL MATCH (f)-[:SUPERSEDES]->(old:Finding)
OPTIONAL MATCH (newer:Finding)-[:SUPERSEDES]->(f)
OPTIONAL MATCH (f)-[:CONTRADICTS]->(x:Finding)
OPTIONAL MATCH (y:Finding)-[:CONTRADICTS]->(f)
RETURN f, collect(DISTINCT e.ref) AS scope,
       collect(DISTINCT {type: s.type, ref: s.ref, note: c.note}) AS cites,
       collect(DISTINCT old.id) AS supersedes, collect(DISTINCT newer.id) AS superseded_by,
       collect(DISTINCT x.id) + collect(DISTINCT y.id) AS contradicts
"""

ENTITY_SEARCH = """
CALL db.index.fulltext.queryNodes('entity_text', $q) YIELD node, score
WHERE $type IS NULL OR node.type = $type
OPTIONAL MATCH (fa:Finding {status: 'active'})-[:SCOPED_TO]->(node)
OPTIONAL MATCH (node)-[:OWNED_BY]->(p:Person)
RETURN node, count(DISTINCT fa) AS active_findings, collect(DISTINCT p.username) AS owner, score
ORDER BY score DESC LIMIT $limit
"""

ENTITY_LIST = """
MATCH (node:Entity)
WHERE $type IS NULL OR node.type = $type
OPTIONAL MATCH (fa:Finding {status: 'active'})-[:SCOPED_TO]->(node)
OPTIONAL MATCH (node)-[:OWNED_BY]->(p:Person)
RETURN node, count(DISTINCT fa) AS active_findings, collect(DISTINCT p.username) AS owner
ORDER BY node.type, node.name LIMIT $limit
"""


class Reader:
    def __init__(self, client: GraphClient, embedder: Embedder | None = None, wiki_base_url: str = "",
                 vector_min_score: float = VECTOR_MIN_SCORE):
        self.client = client
        self.embedder = embedder
        self.wiki_base_url = wiki_base_url.rstrip("/")
        self.vector_min_score = vector_min_score

    def wiki_url(self, finding_id: str) -> str:
        return f"{self.wiki_base_url}/findings/{finding_id}.html"

    def _summary(self, f: dict, scope: list[str], score: float | None = None, matched: list[str] | None = None) -> FindingSummary:
        return FindingSummary(id=f["id"], kind=f["kind"], title=f["title"], claim=f["claim"], scope=sorted(scope),
                              confidence=f["confidence"], status=f["status"], author=f["author"], created=f["created"],
                              wiki_url=self.wiki_url(f["id"]), score=score, matched_scope=sorted(matched or []))

    def findings_for_scope(self, scope: list[str], kinds: list[str] | tuple[str, ...] = (), include_related: bool = True,
                           limit: int = 50) -> list[FindingSummary]:
        rows = self.client.run(SCOPE_QUERY, refs=list(scope), kinds=list(kinds), include_related=include_related, limit=limit)
        return [self._summary(r["f"], r["scope"], matched=r["matched"]) for r in rows]

    def search(self, query: str, scope: list[str] | tuple[str, ...] = (), kinds: list[str] | tuple[str, ...] = (),
               limit: int = 10) -> SearchResult:
        warnings: list[str] = []
        k = 3 * limit
        lucene = lucene_escape(query)
        fulltext_ids = [r["id"] for r in self.client.run(FULLTEXT_QUERY, q=lucene, kinds=list(kinds), k=k)] if lucene else []
        vector_ids: list[str] = []
        if self.embedder is None:
            warnings.append("semantic search disabled (TK_EMBEDDER=none); results are full-text only")
        else:
            try:
                vec = self.embedder.embed([query])[0]
                vector_ids = [r["id"] for r in self.client.run(VECTOR_QUERY, k=k, vec=vec, kinds=list(kinds),
                                                                min_score=self.vector_min_score)]
            except Exception as exc:
                warnings.append(f"semantic search unavailable ({exc}); results are full-text only")
        fused = rrf([fulltext_ids, vector_ids] if vector_ids else [fulltext_ids])
        if not fused:
            return SearchResult([], warnings)
        rows = self.client.run(HYDRATE_QUERY, ids=list(fused))
        wanted = set(scope)
        out = []
        for r in rows:
            score = fused[r["f"]["id"]]
            matched = sorted(set(r["scope"]) & wanted)
            if matched:
                score *= SCOPE_BOOST
            out.append(self._summary(r["f"], r["scope"], score=score, matched=matched))
        out.sort(key=lambda s: (-s.score, s.id))
        return SearchResult(out[:limit], warnings)

    def get_finding(self, finding_id: str) -> dict | None:
        rows = self.client.run(GET_QUERY, id=finding_id)
        if not rows:
            return None
        r = rows[0]
        f = r["f"]
        evidence = [{"type": "observation", "note": n} for n in f.get("observations") or []]
        evidence += [{k: v for k, v in c.items() if v is not None} for c in r["cites"] if c.get("ref")]
        return {
            **self._summary(f, r["scope"]).to_dict(),
            "sections": json.loads(f.get("sections") or "{}"),
            "applies_when": f.get("applies_when"),
            "evidence": evidence,
            "supersedes": sorted(r["supersedes"]),
            "superseded_by": r["superseded_by"][0] if r["superseded_by"] else None,
            "contradicts": sorted(set(r["contradicts"])),
            "retracted_reason": f.get("retracted_reason"),
            "review_after": f.get("review_after"),
            "mr_url": f.get("mr_url"),
            "approved_by": f.get("approved_by") or [],
            "merged_at": f.get("merged_at"),
        }

    def list_entities(self, type: str | None = None, query: str | None = None, limit: int = 20) -> list[dict]:
        if query:
            lucene = lucene_escape(query)
            rows = self.client.run(ENTITY_SEARCH, q=lucene, type=type, limit=limit) if lucene else []
        else:
            rows = self.client.run(ENTITY_LIST, type=type, limit=limit)
        out = []
        for r in rows:
            n = r["node"]
            out.append({"ref": n["ref"], "type": n["type"], "name": n["name"], "aliases": n.get("aliases") or [],
                        "description": n["description"], "owner": sorted(r["owner"]), "active_findings": r["active_findings"]})
        return out
