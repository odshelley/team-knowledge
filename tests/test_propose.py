from datetime import date
from pathlib import Path

import pytest

from teamknowledge.cli import main
from teamknowledge.githost.local import LocalGitHost
from teamknowledge.model import Entity
from teamknowledge.propose import (
    ProposalInput,
    Proposer,
    finding_id_from_branch,
    merge_request_text,
    resolve_reviewers,
)
from teamknowledge.repo import GitError
from teamknowledge.validate import Validator


@pytest.fixture
def proposer(knowledge_repo):
    # add a scoped entity with an owner, committed to main
    e = Entity(type="system", slug="kdb-gateway", name="kdb+ gateway", aliases=["gw"],
               description="The q process that fronts the tick databases for the pricing desk.", owner=["bob"])
    p = knowledge_repo.write_entity(e)
    knowledge_repo.commit_files([p], "add gateway")
    knowledge_repo.push("main")
    host = LocalGitHost(Path(knowledge_repo.remote_url()), author="alice")
    return Proposer(knowledge_repo, Validator(knowledge_repo.schema_dir), host, author="alice", today=lambda: date(2026, 10, 4))


def good_input(**kw) -> ProposalInput:
    base = dict(kind="dead-end", title="Batch queries over 10k symbols time out",
                claim="Selects over more than ten thousand symbols hit the thirty second gateway timeout.",
                scope=["system/kdb-gateway"], evidence=[{"type": "observation", "note": "Reproduced on 2026-10-04 with 12k symbols."}],
                confidence="observed", sections={"Approach": "One select.", "Why it fails": "Timeout."})
    base.update(kw)
    return ProposalInput(**base)


def test_propose_opens_merge_request(proposer, knowledge_repo):
    r = proposer.propose(good_input())
    assert r.errors == [] and r.error is None
    assert r.branch == f"finding/{r.finding_id}"
    assert r.merge_request_url == "local://merge-requests/1"
    assert r.reviewers == ["bob"]
    mr = proposer.host.merge_request_for_branch(r.branch)
    assert mr.state == "open"
    # the clone is back on main and main is untouched
    assert knowledge_repo.git("rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert not any(p.name.startswith(r.finding_id) for p in knowledge_repo.findings_dir.iterdir())
    # the branch has the file with the right header
    names = knowledge_repo.ls_tree(f"origin/{r.branch}", "findings")
    path = next(n for n in names if r.finding_id in n)
    text = knowledge_repo.show(f"origin/{r.branch}", path)
    assert "author: alice" in text and "created: 2026-10-04" in text and "status: active" in text


def test_propose_invalid_returns_errors_and_writes_nothing(proposer, knowledge_repo):
    r = proposer.propose(good_input(scope=["system/kdb-gw"]))
    assert r.branch is None and r.merge_request_url is None
    assert r.errors[0]["field"] == "scope[0]"
    assert "system/kdb-gateway" in r.errors[0]["suggestion"]
    assert knowledge_repo.git("branch", "--list", "finding/*").strip() == ""


def test_propose_with_new_entity_in_same_mr(proposer, knowledge_repo):
    r = proposer.propose(good_input(scope=["system/kdb-gateway", "environment/uat"], new_entities=[
        {"type": "environment", "slug": "uat", "name": "UAT", "description": "User acceptance testing environment for the desk."}]))
    assert r.errors == []
    assert "entities/environment/uat.md" in knowledge_repo.ls_tree(f"origin/{r.branch}", "entities")


def test_propose_supersedes_edits_old_file(proposer, knowledge_repo):
    first = proposer.propose(good_input())
    proposer.host.approve(first.branch, "bob")
    second = proposer.propose(good_input(title="Batch queries over 5k symbols time out", supersedes=[first.finding_id]))
    assert second.errors == []
    old_path = next(n for n in knowledge_repo.ls_tree(f"origin/{second.branch}", "findings") if first.finding_id in n)
    old_text = knowledge_repo.show(f"origin/{second.branch}", old_path)
    assert "status: superseded" in old_text and f"superseded_by: {second.finding_id}" in old_text


def test_propose_supersedes_missing_target(proposer):
    r = proposer.propose(good_input(supersedes=["01J9XK3M8Q7ZV2W1F4N6B5HT9Z"]))
    assert r.errors[0]["field"] == "supersedes"


def test_amend_and_retract(proposer, knowledge_repo):
    first = proposer.propose(good_input())
    proposer.host.approve(first.branch, "bob")
    a = proposer.amend(first.finding_id, "tighten the claim", header_changes={"confidence": "inferred"})
    assert a.errors == [] and a.branch.startswith(f"amend/{first.finding_id}-")
    text = knowledge_repo.show(f"origin/{a.branch}", next(n for n in knowledge_repo.ls_tree(f"origin/{a.branch}", "findings") if first.finding_id in n))
    assert "confidence: inferred" in text
    bad = proposer.amend(first.finding_id, "nope", header_changes={"kind": "fact"})
    assert bad.errors[0]["field"] == "kind"
    r = proposer.retract(first.finding_id, "the gateway was upgraded and the limit is gone")
    assert r.errors == [] and r.branch == f"retract/{first.finding_id}"
    text = knowledge_repo.show(f"origin/{r.branch}", next(n for n in knowledge_repo.ls_tree(f"origin/{r.branch}", "findings") if first.finding_id in n))
    assert "status: retracted" in text


def test_amend_unknown_id(proposer):
    assert proposer.amend("01J9XK3M8Q7ZV2W1F4N6B5HT9Z", "x").errors[0]["field"] == "finding_id"


def test_push_failure_keeps_branch_and_offers_retry(proposer, knowledge_repo, monkeypatch):
    def boom(branch, push_options=()):
        raise GitError("remote unreachable")
    monkeypatch.setattr(knowledge_repo, "push", boom)
    r = proposer.propose(good_input())
    assert r.merge_request_url is None and "remote unreachable" in r.error
    assert r.retry == f"tk push {r.branch}"
    assert knowledge_repo.git("rev-parse", "--verify", r.branch).strip()
    assert knowledge_repo.git("rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    monkeypatch.undo()
    r2 = proposer.push_branch(r.branch)
    assert r2.merge_request_url == "local://merge-requests/1" and r2.finding_id == r.finding_id


def test_push_and_fetch_failure_still_returns_result_on_main(proposer, knowledge_repo, monkeypatch):
    def boom(branch, push_options=()):
        raise GitError("remote unreachable")
    monkeypatch.setattr(knowledge_repo, "push", boom)
    knowledge_repo.git("remote", "set-url", "origin", "/nonexistent/remote.git")
    r = proposer.propose(good_input())
    assert r.merge_request_url is None and "remote unreachable" in r.error
    assert r.retry == f"tk push {r.branch}"
    assert knowledge_repo.git("rev-parse", "--abbrev-ref", "HEAD").strip() == "main"


def test_cli_push(proposer, knowledge_repo, monkeypatch, capsys):
    monkeypatch.setattr(knowledge_repo, "push", lambda *a, **k: (_ for _ in ()).throw(GitError("down")))
    r = proposer.propose(good_input())
    monkeypatch.undo()
    monkeypatch.setenv("TK_GITHOST", "local")
    monkeypatch.setenv("TK_AUTHOR", "alice")
    assert main(["push", r.branch, "--repo", str(knowledge_repo.root)]) == 0
    assert "local://merge-requests/1" in capsys.readouterr().out


def test_retract_twice_keeps_main_checked_out_and_offers_no_retry(proposer, knowledge_repo):
    first = proposer.propose(good_input())
    proposer.host.approve(first.branch, "bob")
    r1 = proposer.retract(first.finding_id, "the gateway was upgraded and the limit is gone")
    assert r1.errors == [] and r1.error is None
    proposer.host.approve(r1.branch, "bob")
    # the finding is already retracted on main; retracting it again writes no new content
    r2 = proposer.retract(first.finding_id, "the gateway was upgraded and the limit is gone")
    assert r2.error is not None and r2.retry is None
    assert knowledge_repo.git("rev-parse", "--abbrev-ref", "HEAD").strip() == "main"


def test_resolve_reviewers():
    e1 = Entity(type="system", slug="a", name="A", description="d" * 20, owner=["bob", "alice"])
    e2 = Entity(type="system", slug="b", name="B", description="d" * 20, owner=["carol"])
    cfg = {"review": {"default_reviewers": ["dave"]}}
    assert resolve_reviewers(["system/a", "system/b"], [e1, e2], cfg, author="alice") == ["bob", "carol"]
    assert resolve_reviewers(["system/zzz"], [e1, e2], cfg, author="alice") == ["dave"]
    assert resolve_reviewers(["system/a"], [Entity(type="system", slug="a", name="A", description="d" * 20)], {}, author="x") == []


def test_merge_request_text_and_branch_id():
    from teamknowledge.model import Evidence, Finding
    f = Finding(id="01J9XK3M8Q7ZV2W1F4N6B5HT9D", kind="fact", title="T", claim="C", scope=["system/a"],
                evidence=[Evidence("confluence", ref="https://c/x")], confidence="observed", status="active",
                author="alice", created=date(2026, 10, 4), sections={"Detail": "D", "How to check": "H"})
    title, desc = merge_request_text(f, reason="why")
    assert title == "[fact] T"
    assert "**Claim.** C" in desc and "`system/a`" in desc and "https://c/x" in desc and "why" in desc and "## Detail" in desc
    title2, desc2 = merge_request_text(f, reason="why", action="Amend")
    assert title2 == "[fact] T"
    assert desc2.startswith("**Action.** Amend")
    assert finding_id_from_branch("amend/01J9XK3M8Q7ZV2W1F4N6B5HT9D-ab12cd") == "01J9XK3M8Q7ZV2W1F4N6B5HT9D"
