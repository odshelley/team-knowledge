"""Measure recall at 3 for full-text only, vector only, and fused retrieval over the fixture corpus."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from .embed.base import Embedder
from .eval_fixtures import ENTITIES, FINDINGS, QUERIES, SECTIONS, finding_id
from .graph.client import GraphClient
from .graph.queries import VECTOR_MIN_SCORE, VECTOR_QUERY, Reader
from .graph.sync import Syncer
from .model import Evidence, Finding
from .repo import init_knowledge_repo

K = 3


def build_fixture_repo(workdir: Path):
    repo = init_knowledge_repo(workdir / "eval-repo", local_remote=workdir / "eval-remote.git", author="eval")
    paths = [repo.write_entity(e) for e in ENTITIES]
    for i, (kind, title, claim, scope) in enumerate(FINDINGS):
        sections = {h: f"{h} for: {claim}" for h in SECTIONS[kind]}
        f = Finding(id=finding_id(i), kind=kind, title=title, claim=claim, scope=scope,
                    evidence=[Evidence("observation", note="Fixture observation written for the retrieval evaluation.")],
                    confidence="observed", status="active", author="eval", created=date(2026, 10, 1), sections=sections)
        paths.append(repo.write_finding(f))
    repo.commit_files(paths, "eval corpus")
    repo.push("main")
    return repo


def recall_at_k(ranked_ids: list[str], expected: list[str], k: int = K) -> float:
    if not expected:
        return 0.0
    hits = len(set(ranked_ids[:k]) & set(expected))
    return hits / len(expected)


def run_eval(client: GraphClient, embedder: Embedder | None, workdir: Path) -> dict:
    repo = build_fixture_repo(Path(workdir))
    Syncer(repo, client, embedder=embedder).sync(full=True)
    fulltext_reader = Reader(client, embedder=None)
    fused_reader = Reader(client, embedder=embedder)
    scores = {"fulltext": 0.0, "vector": 0.0, "fused": 0.0}
    for query, indices in QUERIES:
        expected = [finding_id(i) for i in indices]
        scores["fulltext"] += recall_at_k([f.id for f in fulltext_reader.search(query, limit=K).findings], expected)
        if embedder is not None:
            vec = embedder.embed([query])[0]
            rows = client.run(VECTOR_QUERY, k=K, vec=vec, kinds=[], min_score=VECTOR_MIN_SCORE)
            scores["vector"] += recall_at_k([r["id"] for r in rows], expected)
            scores["fused"] += recall_at_k([f.id for f in fused_reader.search(query, limit=K).findings], expected)
    n = len(QUERIES)
    return {"fulltext": round(scores["fulltext"] / n, 3), "vector": round(scores["vector"] / n, 3),
            "fused": round(scores["fused"] / n, 3), "queries": n, "findings": len(FINDINGS),
            "embedder": embedder.model if embedder else "none"}
