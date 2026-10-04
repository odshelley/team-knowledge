"""Idempotent sync of the knowledge repo into Neo4j. The graph is a function of the files."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..embed.base import Embedder
from ..githost.base import GitHost
from ..model import Entity, Finding
from ..repo import Change, KnowledgeRepo
from ..validate import Validator
from .client import GraphClient
from .schema import drop_vector_index, init_schema, wipe

log = logging.getLogger(__name__)
SCHEMA_VERSION = 1
BATCH = 100


class SyncError(RuntimeError):
    pass


@dataclass
class SyncReport:
    head: str
    unchanged: bool = False
    findings_upserted: int = 0
    entities_upserted: int = 0
    findings_deleted: int = 0
    entities_deleted: int = 0
    embedded: int = 0
    embedding_failures: int = 0
    review_metadata_set: int = 0
    warnings: list[str] = field(default_factory=list)


def text_hash(f: Finding) -> str:
    return hashlib.sha256(f.text_for_embedding().encode()).hexdigest()


def finding_props(f: Finding, commit: str | None) -> dict:
    return {
        "kind": f.kind, "title": f.title, "claim": f.claim, "body": f.body(), "sections": json.dumps(f.sections),
        "applies_when": f.applies_when, "confidence": f.confidence, "status": f.status,
        "retracted_reason": f.retracted_reason, "superseded_by": f.superseded_by, "author": f.author,
        "created": f.created.isoformat(), "review_after": f.review_after.isoformat() if f.review_after else None,
        "path": str(f.path), "commit": commit, "text_hash": text_hash(f), "observations": f.observations(),
    }


def entity_row(e: Entity) -> dict:
    return {
        "ref": e.ref, "type": e.type, "slug": e.slug, "name": e.name, "aliases": list(e.aliases),
        "aliases_text": " ".join(e.aliases), "description": e.description, "body": e.body, "path": str(e.path),
        "related": list(e.related), "owner": list(e.owner), "links": [link.to_dict() for link in e.links],
    }


def _ref_from_path(path: Path) -> str:
    return f"{path.parent.name}/{path.stem}"


ENTITY_UPSERT = """
UNWIND $rows AS row
MERGE (e:Entity {ref: row.ref})
SET e.type = row.type, e.slug = row.slug, e.name = row.name, e.aliases = row.aliases,
    e.aliases_text = row.aliases_text, e.description = row.description, e.body = row.body, e.path = row.path
WITH e, row
OPTIONAL MATCH (e)-[r:RELATED_TO|OWNED_BY|LINKS_TO]->()
DELETE r
WITH DISTINCT e, row
FOREACH (ref IN row.related | MERGE (o:Entity {ref: ref}) MERGE (e)-[:RELATED_TO]->(o))
FOREACH (u IN row.owner | MERGE (p:Person {username: u}) MERGE (e)-[:OWNED_BY]->(p))
FOREACH (l IN row.links |
  MERGE (s:Source {ref: l.ref}) ON CREATE SET s.type = l.type
  MERGE (e)-[x:LINKS_TO]->(s) SET x.note = l.note)
"""

FINDING_UPSERT = """
UNWIND $rows AS row
MERGE (f:Finding {id: row.id})
SET f += row.props
WITH f, row
OPTIONAL MATCH (f)-[r:SCOPED_TO|CITES|SUPERSEDES|CONTRADICTS|AUTHORED_BY]->()
DELETE r
WITH DISTINCT f, row
FOREACH (ref IN row.scope | MERGE (e:Entity {ref: ref}) MERGE (f)-[:SCOPED_TO]->(e))
FOREACH (c IN row.cites |
  MERGE (s:Source {ref: c.ref}) ON CREATE SET s.type = c.type
  MERGE (f)-[x:CITES]->(s) SET x.note = c.note)
FOREACH (sid IN row.supersedes | MERGE (g:Finding {id: sid}) MERGE (f)-[:SUPERSEDES]->(g))
FOREACH (cid IN row.contradicts | MERGE (g:Finding {id: cid}) MERGE (f)-[:CONTRADICTS]->(g))
MERGE (p:Person {username: row.author})
MERGE (f)-[:AUTHORED_BY]->(p)
"""

EXISTING_EMBEDDINGS = """
UNWIND $ids AS id
MATCH (f:Finding {id: id})
RETURN f.id AS id, f.text_hash AS text_hash, f.embedding_model AS model, f.embedding IS NOT NULL AS has
"""

REVIEW_METADATA = """
UNWIND $rows AS row
MATCH (f:Finding {id: row.id})
SET f.mr_url = row.mr_url, f.approved_by = row.approved_by, f.merged_at = row.merged_at
"""

WRITE_META = """
MERGE (m:Meta {key: 'meta'})
SET m.schema_version = $schema_version, m.last_sync_commit = $sha,
    m.embedding_model = $model, m.embedding_dims = $dims, m.synced_at = datetime()
"""


class Syncer:
    def __init__(self, repo: KnowledgeRepo, client: GraphClient, embedder: Embedder | None = None,
                 host: GitHost | None = None, sleep: Callable[[float], None] = time.sleep):
        self.repo = repo
        self.client = client
        self.embedder = embedder
        self.host = host
        self.sleep = sleep

    def meta(self) -> dict | None:
        rows = self.client.run("MATCH (m:Meta {key: 'meta'}) RETURN m")
        return rows[0]["m"] if rows else None

    # --- main entry points -------------------------------------------------

    def sync(self, full: bool = False) -> SyncReport:
        meta = self.meta()
        model = self.embedder.model if self.embedder else None
        dims = self.embedder.dims if self.embedder else None
        if not full and meta is not None and meta.get("embedding_model") != model:
            raise SyncError(f"embedding model changed (graph has {meta.get('embedding_model')!r}, "
                            f"configured {model!r}); run tk sync --full")
        head = self.repo.head_sha()
        report = SyncReport(head=head)
        findings, entities, errors = self.repo.load_with_errors()
        errors += Validator(self.repo.schema_dir).validate_all(findings, entities)
        if errors:
            e = errors[0]
            raise SyncError(f"{len(errors)} validation errors; first: {e.path}: {e.field}: {e.message}")
        last = meta.get("last_sync_commit") if meta else None
        if full or last is None:
            if full:
                wipe(self.client)
                drop_vector_index(self.client)
            init_schema(self.client, dims)
            changed_f, changed_e, deleted_f, deleted_e = findings, entities, [], []
        else:
            if last == head:
                report.unchanged = True
                return report
            changed_f, changed_e, deleted_f, deleted_e = self._partition(self.repo.changed_files(last, head), findings, entities)
        self._upsert_entities(changed_e)
        report.entities_upserted = len(changed_e)
        if deleted_e:
            self.client.run("UNWIND $refs AS ref MATCH (e:Entity {ref: ref}) DETACH DELETE e", refs=deleted_e)
        report.entities_deleted = len(deleted_e)
        self._upsert_findings(changed_f, report)
        if deleted_f:
            self.client.run("UNWIND $ids AS id MATCH (f:Finding {id: id}) DETACH DELETE f", ids=deleted_f)
        report.findings_deleted = len(deleted_f)
        self._review_metadata(changed_f, report)
        self.client.run(WRITE_META, schema_version=SCHEMA_VERSION, sha=head, model=model, dims=dims)
        return report

    def embed_missing(self) -> int:
        if self.embedder is None:
            raise SyncError("TK_EMBEDDER is none; nothing to embed")
        meta = self.meta()
        graph_model = meta.get("embedding_model") if meta else None
        if graph_model and graph_model != self.embedder.model:
            raise SyncError(f"embedding model changed (graph has {graph_model!r}, "
                            f"configured {self.embedder.model!r}); run tk sync --full")
        ids = {r["id"] for r in self.client.run("MATCH (f:Finding) WHERE f.embedding IS NULL RETURN f.id AS id")}
        findings = [f for f in self.repo.load_findings() if f.id in ids]
        if not findings:
            return 0
        vectors = self._embed_with_retry([f.text_for_embedding() for f in findings])
        rows = [{"id": f.id, "embedding": v, "model": self.embedder.model, "text_hash": text_hash(f)} for f, v in zip(findings, vectors)]
        for i in range(0, len(rows), BATCH):
            self.client.run("UNWIND $rows AS row MATCH (f:Finding {id: row.id}) "
                            "SET f.embedding = row.embedding, f.embedding_model = row.model, f.text_hash = row.text_hash",
                            rows=rows[i:i + BATCH])
        return len(rows)

    # --- internals ---------------------------------------------------------

    @staticmethod
    def _partition(changes: list[Change], findings: list[Finding], entities: list[Entity]):
        f_by_path = {str(f.path): f for f in findings}
        e_by_path = {str(e.path): e for e in entities}
        changed_f: list[Finding] = []
        changed_e: list[Entity] = []
        deleted_f: list[str] = []
        deleted_e: list[str] = []
        for c in changes:
            p = str(c.path)
            if c.status == "D":
                if p.startswith("findings/"):
                    deleted_f.append(c.path.stem[:26])
                else:
                    deleted_e.append(_ref_from_path(c.path))
                continue
            if c.status == "R" and c.old_path is not None and str(c.old_path).startswith("entities/"):
                deleted_e.append(_ref_from_path(c.old_path))
            if p in f_by_path:
                changed_f.append(f_by_path[p])
            elif p in e_by_path:
                changed_e.append(e_by_path[p])
        return changed_f, changed_e, deleted_f, deleted_e

    def _upsert_entities(self, entities: list[Entity]) -> None:
        rows = [entity_row(e) for e in entities]
        for i in range(0, len(rows), BATCH):
            self.client.run(ENTITY_UPSERT, rows=rows[i:i + BATCH])

    def _upsert_findings(self, findings: list[Finding], report: SyncReport) -> None:
        if not findings:
            return
        vectors = self._embeddings(findings, report)
        rows = []
        for f in findings:
            props = finding_props(f, self.repo.last_commit_for(f.path))
            if f.id in vectors:
                props["embedding"] = vectors[f.id]
                props["embedding_model"] = self.embedder.model if vectors[f.id] is not None else None
            rows.append({"id": f.id, "props": props, "scope": list(f.scope), "cites": [e.to_dict() for e in f.references()],
                         "supersedes": list(f.supersedes), "contradicts": list(f.contradicts), "author": f.author})
        for i in range(0, len(rows), BATCH):
            self.client.run(FINDING_UPSERT, rows=rows[i:i + BATCH])
        report.findings_upserted = len(rows)

    def _embeddings(self, findings: list[Finding], report: SyncReport) -> dict[str, list[float] | None]:
        """Vectors for findings whose embedding must be (re)computed; ids absent keep their stored vector."""
        if self.embedder is None:
            return {}
        existing = {r["id"]: r for r in self.client.run(EXISTING_EMBEDDINGS, ids=[f.id for f in findings])}
        need = [f for f in findings
                if not ((ex := existing.get(f.id)) and ex["has"] and ex["text_hash"] == text_hash(f) and ex["model"] == self.embedder.model)]
        if not need:
            return {}
        try:
            vectors = self._embed_with_retry([f.text_for_embedding() for f in need])
        except Exception as exc:
            report.embedding_failures = len(need)
            report.warnings.append(f"embedding failed after retries ({exc}); stored without embeddings, run tk sync --embed-missing")
            return {f.id: None for f in need}
        report.embedded = len(need)
        return {f.id: v for f, v in zip(need, vectors)}

    def _embed_with_retry(self, texts: list[str], attempts: int = 3) -> list[list[float]]:
        delay = 1.0
        for attempt in range(1, attempts + 1):
            try:
                return self.embedder.embed(texts)
            except Exception:
                if attempt == attempts:
                    raise
                log.warning("embedding attempt %d failed; retrying in %.0fs", attempt, delay)
                self.sleep(delay)
                delay *= 2
        raise AssertionError("unreachable")

    def _review_metadata(self, findings: list[Finding], report: SyncReport) -> None:
        if self.host is None or not findings:
            return
        rows = []
        for f in findings:
            sha = self.repo.last_commit_for(f.path)
            try:
                merge_requests = self.host.merge_requests_for_commit(sha) if sha else []
            except Exception as exc:
                report.warnings.append(f"review metadata lookup failed for {f.id}: {exc}")
                continue
            merged = [m for m in merge_requests if m.state == "merged"]
            if not merged:
                continue
            m = merged[0]
            rows.append({"id": f.id, "mr_url": m.url, "approved_by": list(m.approvers),
                         "merged_at": m.merged_at.isoformat() if m.merged_at else None})
        for i in range(0, len(rows), BATCH):
            self.client.run(REVIEW_METADATA, rows=rows[i:i + BATCH])
        report.review_metadata_set = len(rows)
