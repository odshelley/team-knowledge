"""GitLab as a git host: REST API mode and push-option mode."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

import requests

from .base import GitHostError, MergeRequest

_STATE = {"opened": "open", "merged": "merged", "closed": "closed", "locked": "open"}


class GitLabHost:
    def __init__(self, url: str, token: str, project: str, mode: str = "api",
                 target_branch: str = "main", session=None, timeout: float = 20.0):
        if mode not in ("api", "push-options"):
            raise ValueError(f"mode must be 'api' or 'push-options', got {mode!r}")
        self.api = url.rstrip("/") + "/api/v4"
        self.project_id = quote(project, safe="")
        self.mode = mode
        self.target_branch = target_branch
        self.session = session or requests.Session()
        self.headers = {"PRIVATE-TOKEN": token}
        self.timeout = timeout

    # --- http --------------------------------------------------------------

    def _get(self, path: str, **params):
        try:
            r = self.session.get(self.api + path, params=params or None, headers=self.headers, timeout=self.timeout)
        except requests.RequestException as exc:
            raise GitHostError(f"GitLab request failed: {exc}") from exc
        if r.status_code >= 400:
            raise GitHostError(f"GitLab GET {path} returned {r.status_code}: {r.text[:200]}")
        return r.json()

    def _post(self, path: str, payload: dict):
        try:
            r = self.session.post(self.api + path, json=payload, headers=self.headers, timeout=self.timeout)
        except requests.RequestException as exc:
            raise GitHostError(f"GitLab request failed: {exc}") from exc
        if r.status_code >= 400:
            raise GitHostError(f"GitLab POST {path} returned {r.status_code}: {r.text[:200]}")
        return r.json()

    def _mr_path(self, suffix: str = "") -> str:
        return f"/projects/{self.project_id}/merge_requests{suffix}"

    def _user_id(self, username: str) -> int | None:
        users = self._get("/users", username=username)
        return users[0]["id"] if users else None

    def _to_mr(self, data: dict) -> MergeRequest:
        approvals = self._get(self._mr_path(f"/{data['iid']}/approvals"))
        approvers = [a["user"]["username"] for a in approvals.get("approved_by", [])]
        merged_at = data.get("merged_at")
        return MergeRequest(
            iid=data["iid"], url=data["web_url"], branch=data["source_branch"],
            state=_STATE.get(data["state"], data["state"]), author=data["author"]["username"],
            approvers=approvers,
            merged_at=datetime.fromisoformat(merged_at) if merged_at else None,
            merge_commit=data.get("merge_commit_sha") or data.get("squash_commit_sha"),
        )

    # --- GitHost -----------------------------------------------------------

    def push_options(self, title: str, description: str, reviewers: list[str]) -> list[str]:
        if self.mode != "push-options":
            return []
        opts = ["merge_request.create", f"merge_request.target={self.target_branch}", f"merge_request.title={title}"]
        opts += [f"merge_request.assign={r}" for r in reviewers]
        return opts

    def open_merge_request(self, branch: str, title: str, description: str, reviewers: list[str]) -> MergeRequest:
        if self.mode == "push-options":
            mr = self.merge_request_for_branch(branch)
            if mr is None:
                raise GitHostError(f"push did not create a merge request for {branch}; check push options are enabled")
            return mr
        ids = [uid for uid in (self._user_id(u) for u in reviewers) if uid is not None]
        data = self._post(self._mr_path(), {
            "source_branch": branch, "target_branch": self.target_branch, "title": title,
            "description": description, "reviewer_ids": ids, "remove_source_branch": True,
        })
        return self._to_mr(data)

    def merge_request_for_branch(self, branch: str) -> MergeRequest | None:
        items = self._get(self._mr_path(), source_branch=branch, state="all", order_by="updated_at", sort="desc")
        return self._to_mr(items[0]) if items else None

    def merge_requests_for_commit(self, sha: str) -> list[MergeRequest]:
        items = self._get(f"/projects/{self.project_id}/repository/commits/{sha}/merge_requests")
        return [self._to_mr(i) for i in items]

    def list_open_by(self, username: str) -> list[MergeRequest]:
        items = self._get(self._mr_path(), state="opened", author_username=username)
        return [self._to_mr(i) for i in items]
