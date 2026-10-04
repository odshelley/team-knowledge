from pathlib import Path

import pytest

from teamknowledge.cli import main
from teamknowledge.githost.base import GitHostError
from teamknowledge.githost.local import LocalGitHost


def push_branch(repo, name: str, content: str) -> str:
    repo.create_branch(name)
    p = repo.root / "findings" / f"{name.split('/')[-1]}.txt"
    p.write_text(content)
    sha = repo.commit_files([p.relative_to(repo.root)], f"add {name}")
    repo.push(name)
    repo.sync_main()
    return sha


def test_open_and_lookup(knowledge_repo):
    host = LocalGitHost(Path(knowledge_repo.remote_url()), author="alice")
    push_branch(knowledge_repo, "finding/one", "x")
    mr = host.open_merge_request("finding/one", "[fact] One", "desc", ["bob"])
    assert mr.state == "open" and mr.author == "alice" and mr.iid == 1
    assert host.merge_request_for_branch("finding/one") == mr
    assert host.merge_request_for_branch("finding/none") is None
    assert host.list_open_by("alice") == [mr]
    assert host.list_open_by("bob") == []
    assert host.push_options("t", "d", ["bob"]) == []


def test_author_cannot_approve(knowledge_repo):
    host = LocalGitHost(Path(knowledge_repo.remote_url()), author="alice")
    push_branch(knowledge_repo, "finding/one", "x")
    host.open_merge_request("finding/one", "t", "d", [])
    with pytest.raises(GitHostError, match="own merge request"):
        host.approve("finding/one", "alice")


def test_approve_creates_merge_commit_on_main(knowledge_repo):
    host = LocalGitHost(Path(knowledge_repo.remote_url()), author="alice")
    head = push_branch(knowledge_repo, "finding/one", "x")
    host.open_merge_request("finding/one", "t", "d", [])
    mr = host.approve("finding/one", "bob")
    assert mr.state == "merged" and mr.approvers == ["bob"] and mr.merged_at is not None
    knowledge_repo.sync_main()
    assert knowledge_repo.head_sha() == mr.merge_commit
    parents = knowledge_repo.git("log", "-1", "--format=%P").split()
    assert head in parents and len(parents) == 2
    assert (knowledge_repo.root / "findings" / "one.txt").read_text() == "x"
    assert host.merge_requests_for_commit(mr.merge_commit) == [mr]
    assert knowledge_repo.last_commit_for(Path("findings/one.txt")) == mr.merge_commit


def test_approve_twice_refused(knowledge_repo):
    host = LocalGitHost(Path(knowledge_repo.remote_url()), author="alice")
    push_branch(knowledge_repo, "finding/one", "x")
    host.open_merge_request("finding/one", "t", "d", [])
    host.approve("finding/one", "bob")
    with pytest.raises(GitHostError, match="already merged"):
        host.approve("finding/one", "bob")


def test_approve_refuses_stale_branch(knowledge_repo):
    host = LocalGitHost(Path(knowledge_repo.remote_url()), author="alice")
    push_branch(knowledge_repo, "finding/one", "x")
    push_branch(knowledge_repo, "finding/two", "y")
    host.open_merge_request("finding/one", "t", "d", [])
    host.open_merge_request("finding/two", "t", "d", [])
    host.approve("finding/one", "bob")
    with pytest.raises(GitHostError, match="behind main"):
        host.approve("finding/two", "bob")


def test_cli_review(knowledge_repo, capsys):
    host = LocalGitHost(Path(knowledge_repo.remote_url()), author="alice")
    push_branch(knowledge_repo, "finding/one", "x")
    host.open_merge_request("finding/one", "[fact] One", "d", ["bob"])
    assert main(["review", "list", "--repo", str(knowledge_repo.root)]) == 0
    assert "finding/one" in capsys.readouterr().out
    assert main(["review", "approve", "finding/one", "--as", "bob", "--repo", str(knowledge_repo.root)]) == 0
    assert "merged" in capsys.readouterr().out
    assert main(["review", "approve", "finding/one", "--as", "bob", "--repo", str(knowledge_repo.root)]) == 1
