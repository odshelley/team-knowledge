"""What a git hosting service adds on top of git: merge requests and their review state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class GitHostError(RuntimeError):
    pass


@dataclass(frozen=True)
class MergeRequest:
    iid: int | str
    url: str
    branch: str
    state: str  # open | merged | closed
    author: str
    approvers: list[str]
    merged_at: datetime | None
    merge_commit: str | None


class GitHost(Protocol):
    def push_options(self, title: str, description: str, reviewers: list[str]) -> list[str]:
        """Extra `git push -o` options; empty unless the host creates merge requests from pushes."""

    def open_merge_request(self, branch: str, title: str, description: str, reviewers: list[str]) -> MergeRequest: ...

    def merge_request_for_branch(self, branch: str) -> MergeRequest | None: ...

    def merge_requests_for_commit(self, sha: str) -> list[MergeRequest]: ...

    def list_open_by(self, username: str) -> list[MergeRequest]: ...
