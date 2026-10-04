"""A git host for the home prototype and tests: a bare repo plus a JSON sidecar per merge request."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from .base import GitHostError, MergeRequest


class LocalGitHost:
    def __init__(self, remote: Path, author: str):
        self.remote = Path(remote).resolve()
        self.author = author
        self.review_dir = self.remote.parent / (self.remote.name + ".review")
        self.review_dir.mkdir(parents=True, exist_ok=True)

    # --- git helpers -------------------------------------------------------

    def _run(self, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(self.remote), *args], capture_output=True, text=True, env=env, check=False)

    def _git(self, *args: str, env: dict | None = None) -> str:
        result = self._run(*args, env=env)
        if result.returncode != 0:
            raise GitHostError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout.strip()

    # --- records -----------------------------------------------------------

    def _record_path(self, branch: str) -> Path:
        return self.review_dir / (branch.replace("/", "__") + ".json")

    def _load(self, branch: str) -> dict | None:
        p = self._record_path(branch)
        return json.loads(p.read_text()) if p.exists() else None

    def _save(self, rec: dict) -> None:
        self._record_path(rec["branch"]).write_text(json.dumps(rec, indent=2, sort_keys=True))

    def _records(self) -> list[dict]:
        return sorted((json.loads(p.read_text()) for p in self.review_dir.glob("*.json")), key=lambda r: r["iid"])

    @staticmethod
    def _to_mr(rec: dict) -> MergeRequest:
        merged_at = datetime.fromisoformat(rec["merged_at"]) if rec.get("merged_at") else None
        return MergeRequest(iid=rec["iid"], url=rec["url"], branch=rec["branch"], state=rec["state"],
                            author=rec["author"], approvers=list(rec["approvers"]), merged_at=merged_at,
                            merge_commit=rec.get("merge_commit"))

    # --- GitHost -----------------------------------------------------------

    def push_options(self, title: str, description: str, reviewers: list[str]) -> list[str]:
        return []

    def open_merge_request(self, branch: str, title: str, description: str, reviewers: list[str]) -> MergeRequest:
        if self._load(branch) is not None:
            raise GitHostError(f"a merge request for {branch} already exists")
        iid = len(self._records()) + 1
        rec = {
            "iid": iid, "url": f"local://merge-requests/{iid}", "branch": branch, "title": title,
            "description": description, "reviewers": list(reviewers), "author": self.author, "state": "open",
            "approvers": [], "merged_at": None, "merge_commit": None, "head_sha": self._git("rev-parse", branch),
        }
        self._save(rec)
        return self._to_mr(rec)

    def merge_request_for_branch(self, branch: str) -> MergeRequest | None:
        rec = self._load(branch)
        return self._to_mr(rec) if rec else None

    def merge_requests_for_commit(self, sha: str) -> list[MergeRequest]:
        return [self._to_mr(r) for r in self._records() if sha in (r.get("merge_commit"), r.get("head_sha"))]

    def list_open_by(self, username: str) -> list[MergeRequest]:
        return [self._to_mr(r) for r in self._records() if r["author"] == username and r["state"] == "open"]

    def list_all(self) -> list[MergeRequest]:
        return [self._to_mr(r) for r in self._records()]

    # --- review ------------------------------------------------------------

    def approve(self, branch: str, username: str) -> MergeRequest:
        rec = self._load(branch)
        if rec is None:
            raise GitHostError(f"no merge request for branch {branch}")
        if rec["state"] != "open":
            raise GitHostError(f"merge request for {branch} is already {rec['state']}")
        if username == rec["author"]:
            raise GitHostError("authors cannot approve their own merge request")
        if self._run("merge-base", "--is-ancestor", "main", branch).returncode != 0:
            raise GitHostError(f"{branch} is behind main; rebase it first")
        main = self._git("rev-parse", "main")
        head = self._git("rev-parse", branch)
        tree = self._git("rev-parse", f"{branch}^{{tree}}")
        env = {**os.environ, "GIT_AUTHOR_NAME": username, "GIT_AUTHOR_EMAIL": f"{username}@users.noreply.local",
               "GIT_COMMITTER_NAME": username, "GIT_COMMITTER_EMAIL": f"{username}@users.noreply.local"}
        merge_sha = self._git("commit-tree", tree, "-p", main, "-p", head, "-m",
                              f"Merge {branch}\n\nApproved-by: {username}", env=env)
        self._git("update-ref", "refs/heads/main", merge_sha, main)
        rec.update(state="merged", approvers=[username], merge_commit=merge_sha, head_sha=head,
                   merged_at=datetime.now(UTC).isoformat())
        self._save(rec)
        return self._to_mr(rec)
