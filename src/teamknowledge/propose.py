"""Turn a finding into a branch and a merge request; amend, retract, and retry pushes."""

from __future__ import annotations

import copy
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .githost.base import GitHost
from .model import Entity, Evidence, Finding, new_ulid, parse_finding
from .repo import KnowledgeRepo
from .validate import ValidationError, Validator

AMENDABLE = frozenset({"title", "claim", "scope", "applies_when", "evidence", "confidence", "contradicts", "review_after"})


@dataclass
class ProposalInput:
    kind: str
    title: str
    claim: str
    scope: list[str]
    evidence: list[dict]
    confidence: str
    sections: dict[str, str]
    applies_when: str | None = None
    supersedes: list[str] = field(default_factory=list)
    review_after: str | None = None
    new_entities: list[dict] = field(default_factory=list)


@dataclass
class ProposalResult:
    finding_id: str | None = None
    branch: str | None = None
    merge_request_url: str | None = None
    reviewers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    error: str | None = None
    retry: str | None = None

    def to_dict(self) -> dict:
        if self.errors:
            return {"errors": self.errors}
        d = {"finding_id": self.finding_id, "branch": self.branch, "merge_request_url": self.merge_request_url,
             "reviewers": self.reviewers, "warnings": self.warnings}
        if self.error:
            d["error"] = self.error
            d["retry"] = self.retry
        return d


def finding_id_from_branch(branch: str) -> str:
    return branch.split("/", 1)[1][:26]


def resolve_reviewers(scope: list[str], entities: list[Entity], config: dict, author: str) -> list[str]:
    by_ref = {e.ref: e for e in entities}
    owners = sorted({u for ref in scope if ref in by_ref for u in by_ref[ref].owner})
    if not owners:
        owners = list(config.get("review", {}).get("default_reviewers", []) or [])
    return [u for u in owners if u != author]


def merge_request_text(f: Finding, reason: str | None = None) -> tuple[str, str]:
    title = f"[{f.kind}] {f.title}"
    lines = [f"**Claim.** {f.claim}", "", "**Scope.** " + ", ".join(f"`{s}`" for s in f.scope),
             f"**Confidence.** {f.confidence}", ""]
    if f.applies_when:
        lines += [f"**Applies when.** {f.applies_when}", ""]
    lines += ["**Evidence.**"] + [f"- {e.type}: {e.ref or ''} {e.note or ''}".rstrip() for e in f.evidence] + [""]
    if f.supersedes:
        lines += ["**Supersedes.** " + ", ".join(f.supersedes), ""]
    if reason:
        lines += [f"**Reason for change.** {reason}", ""]
    lines.append(f.body())
    return title, "\n".join(lines)


def _entity_from_dict(d: dict) -> Entity:
    return Entity(type=str(d.get("type", "")), slug=str(d.get("slug", "")), name=str(d.get("name", "")),
                  description=str(d.get("description", "")), aliases=list(d.get("aliases", []) or []),
                  owner=list(d.get("owner", []) or []), related=list(d.get("related", []) or []),
                  links=[Evidence(type=str(x.get("type", "")), ref=x.get("ref"), note=x.get("note")) for x in d.get("links", []) or []],
                  body=str(d.get("body", "") or ""))


def _evidence_list(items: list[dict]) -> list[Evidence]:
    return [Evidence(type=str(x.get("type", "")), ref=x.get("ref"), note=x.get("note")) for x in items]


def _errors(errs: list[ValidationError]) -> ProposalResult:
    return ProposalResult(errors=[e.to_dict() for e in errs])


class Proposer:
    def __init__(self, repo: KnowledgeRepo, validator: Validator, host: GitHost, author: str,
                 today: Callable[[], date] = date.today):
        self.repo = repo
        self.validator = validator
        self.host = host
        self.author = author
        self.today = today

    # --- public ------------------------------------------------------------

    def propose(self, inp: ProposalInput) -> ProposalResult:
        self.repo.sync_main()
        findings, entities = self.repo.load_findings(), self.repo.load_entities()
        previous = {f.id: copy.deepcopy(f) for f in findings}
        fid = new_ulid()
        try:
            review_after = date.fromisoformat(inp.review_after) if inp.review_after else None
        except ValueError:
            return _errors([ValidationError("review_after", f"not an ISO date: {inp.review_after!r}")])
        new = Finding(id=fid, kind=inp.kind, title=inp.title.strip(), claim=inp.claim.strip(), scope=list(inp.scope),
                      evidence=_evidence_list(inp.evidence), confidence=inp.confidence, status="active",
                      author=self.author, created=self.today(), sections=dict(inp.sections),
                      applies_when=inp.applies_when or None, supersedes=list(inp.supersedes), review_after=review_after)
        by_id = {f.id: f for f in findings}
        missing = [sid for sid in inp.supersedes if sid not in by_id]
        if missing:
            return _errors([ValidationError("supersedes", f"finding {sid} does not exist") for sid in missing])
        changed_old: list[Finding] = []
        for sid in inp.supersedes:
            old = by_id[sid]
            old.status, old.superseded_by, old.raw_header = "superseded", fid, None
            changed_old.append(old)
        new_entities = [_entity_from_dict(d) for d in inp.new_entities]
        errs = self.validator.validate_all(findings + [new], entities + new_entities, previous)
        if errs:
            self.repo.sync_main()
            return _errors(errs)
        branch = f"finding/{fid}"
        self.repo.create_branch(branch)
        paths = [self.repo.write_entity(e) for e in new_entities]
        paths += [self.repo.write_finding(new)] + [self.repo.write_finding(o) for o in changed_old]
        title, desc = merge_request_text(new)
        reviewers = resolve_reviewers(new.scope, entities + new_entities, self.repo.config, self.author)
        return self._submit(fid, branch, paths, f"feat: {title}", title, desc, reviewers)

    def amend(self, finding_id: str, reason: str, header_changes: dict | None = None,
              sections: dict | None = None) -> ProposalResult:
        self.repo.sync_main()
        findings, entities = self.repo.load_findings(), self.repo.load_entities()
        previous = {f.id: copy.deepcopy(f) for f in findings}
        target = next((f for f in findings if f.id == finding_id), None)
        if target is None:
            return _errors([ValidationError("finding_id", f"finding {finding_id} does not exist")])
        bad = [k for k in (header_changes or {}) if k not in AMENDABLE]
        if bad:
            return _errors([ValidationError(k, f"'{k}' cannot be amended; allowed: {', '.join(sorted(AMENDABLE))}") for k in bad])
        for k, v in (header_changes or {}).items():
            if k == "evidence":
                target.evidence = _evidence_list(v)
            elif k == "review_after":
                target.review_after = date.fromisoformat(v) if v else None
            elif k in ("scope", "contradicts"):
                setattr(target, k, list(v))
            else:
                setattr(target, k, v)
        if sections is not None:
            target.sections = dict(sections)
        target.raw_header = None
        errs = self.validator.validate_all(findings, entities, previous)
        if errs:
            self.repo.sync_main()
            return _errors(errs)
        branch = f"amend/{finding_id}-{secrets.token_hex(3)}"
        self.repo.create_branch(branch)
        paths = [self.repo.write_finding(target)]
        title, desc = merge_request_text(target, reason)
        reviewers = resolve_reviewers(target.scope, entities, self.repo.config, self.author)
        return self._submit(finding_id, branch, paths, f"fix: amend {title}", f"Amend {title}", desc, reviewers)

    def retract(self, finding_id: str, reason: str) -> ProposalResult:
        self.repo.sync_main()
        findings, entities = self.repo.load_findings(), self.repo.load_entities()
        previous = {f.id: copy.deepcopy(f) for f in findings}
        target = next((f for f in findings if f.id == finding_id), None)
        if target is None:
            return _errors([ValidationError("finding_id", f"finding {finding_id} does not exist")])
        target.status, target.retracted_reason, target.raw_header = "retracted", reason, None
        errs = self.validator.validate_all(findings, entities, previous)
        if errs:
            self.repo.sync_main()
            return _errors(errs)
        branch = f"retract/{finding_id}"
        self.repo.create_branch(branch)
        paths = [self.repo.write_finding(target)]
        title, desc = merge_request_text(target, reason)
        reviewers = resolve_reviewers(target.scope, entities, self.repo.config, self.author)
        return self._submit(finding_id, branch, paths, f"fix: retract {title}", f"Retract {title}", desc, reviewers)

    def push_branch(self, branch: str) -> ProposalResult:
        fid = finding_id_from_branch(branch)
        names = [n for n in self.repo.ls_tree(branch, "findings") if fid in n]
        if not names:
            return ProposalResult(finding_id=fid, branch=branch, error=f"no finding {fid} on branch {branch}")
        f = parse_finding(self.repo.show(branch, names[0]), Path(names[0]))
        title, desc = merge_request_text(f)
        reviewers = resolve_reviewers(f.scope, self.repo.load_entities(), self.repo.config, self.author)
        return self._push_and_open(fid, branch, title, desc, reviewers)

    # --- internals ---------------------------------------------------------

    def _submit(self, fid: str, branch: str, paths: list[Path], commit_message: str,
                title: str, description: str, reviewers: list[str]) -> ProposalResult:
        self.repo.commit_files(paths, commit_message)
        return self._push_and_open(fid, branch, title, description, reviewers)

    def _push_and_open(self, fid: str, branch: str, title: str, description: str, reviewers: list[str]) -> ProposalResult:
        warnings: list[str] = []
        if not reviewers:
            warnings.append("no reviewers resolved: set owner on a scoped entity or review.default_reviewers in config.yaml")
        try:
            existing = self.host.merge_request_for_branch(branch)
            if existing is None:
                self.repo.push(branch, self.host.push_options(title, description, reviewers))
                mr = self.host.open_merge_request(branch, title, description, reviewers)
            else:
                self.repo.push(branch, [])
                mr = existing
        except Exception as exc:  # git or host failure: the branch is committed locally, so offer a retry
            return ProposalResult(finding_id=fid, branch=branch, reviewers=reviewers, warnings=warnings,
                                  error=f"{type(exc).__name__}: {exc}", retry=f"tk push {branch}")
        finally:
            self.repo.sync_main()
        return ProposalResult(finding_id=fid, branch=branch, merge_request_url=mr.url, reviewers=reviewers, warnings=warnings)
