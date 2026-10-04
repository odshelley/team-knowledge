import os
from datetime import UTC, datetime

import pytest
import requests

from teamknowledge.githost.base import GitHostError
from teamknowledge.githost.gitlab import GitLabHost


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class FakeSession:
    """Routes (method, path) to canned payloads and records calls."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def _handle(self, method, url, params=None, json=None, headers=None, timeout=None):
        path = url.split("/api/v4", 1)[1]
        self.calls.append((method, path, params, json, headers))
        key = (method, path)
        if key not in self.routes:
            return FakeResponse(404, {"message": "404 Not Found"})
        payload = self.routes[key]
        return FakeResponse(200, payload(params) if callable(payload) else payload)

    def get(self, url, params=None, headers=None, timeout=None):
        return self._handle("GET", url, params=params, headers=headers, timeout=timeout)

    def post(self, url, json=None, headers=None, timeout=None):
        return self._handle("POST", url, json=json, headers=headers, timeout=timeout)


PID = "pricing%2Fteam-knowledge"
MR = {"iid": 7, "web_url": "https://gitlab.bank/pricing/team-knowledge/-/merge_requests/7",
      "source_branch": "finding/01J9", "state": "merged", "author": {"username": "alice"},
      "merged_at": "2026-10-04T10:00:00.000Z", "merge_commit_sha": "abc123", "squash_commit_sha": None}
APPROVALS = {"approved_by": [{"user": {"username": "bob"}}]}


def host(routes, mode="api"):
    return GitLabHost("https://gitlab.bank", "tok", "pricing/team-knowledge", mode=mode, session=FakeSession(routes))


def test_open_merge_request_resolves_reviewer_ids_and_sets_token():
    routes = {
        ("GET", "/users"): lambda params: [{"id": 42, "username": params["username"]}],
        ("POST", f"/projects/{PID}/merge_requests"): {**MR, "state": "opened", "merged_at": None, "merge_commit_sha": None},
        ("GET", f"/projects/{PID}/merge_requests/7/approvals"): {"approved_by": []},
    }
    h = host(routes)
    mr = h.open_merge_request("finding/01J9", "[fact] T", "desc", ["bob"])
    assert mr.state == "open" and mr.iid == 7 and mr.author == "alice" and mr.approvers == []
    post = next(c for c in h.session.calls if c[0] == "POST")
    assert post[3] == {"source_branch": "finding/01J9", "target_branch": "main", "title": "[fact] T",
                       "description": "desc", "reviewer_ids": [42], "remove_source_branch": True}
    assert post[4]["PRIVATE-TOKEN"] == "tok"


def test_unknown_reviewer_is_skipped():
    routes = {
        ("GET", "/users"): [],
        ("POST", f"/projects/{PID}/merge_requests"): {**MR, "state": "opened"},
        ("GET", f"/projects/{PID}/merge_requests/7/approvals"): {"approved_by": []},
    }
    h = host(routes)
    h.open_merge_request("finding/01J9", "t", "d", ["ghost"])
    post = next(c for c in h.session.calls if c[0] == "POST")
    assert post[3]["reviewer_ids"] == []


def test_merge_request_for_branch_parses_merged_state():
    routes = {
        ("GET", f"/projects/{PID}/merge_requests"): lambda params: [MR] if params.get("source_branch") == "finding/01J9" else [],
        ("GET", f"/projects/{PID}/merge_requests/7/approvals"): APPROVALS,
    }
    mr = host(routes).merge_request_for_branch("finding/01J9")
    assert mr.state == "merged" and mr.approvers == ["bob"] and mr.merge_commit == "abc123"
    assert mr.merged_at == datetime(2026, 10, 4, 10, 0, tzinfo=UTC)
    assert host(routes).merge_request_for_branch("finding/none") is None


def test_merge_requests_for_commit_and_list_open_by():
    routes = {
        ("GET", f"/projects/{PID}/repository/commits/abc123/merge_requests"): [MR],
        ("GET", f"/projects/{PID}/merge_requests/7/approvals"): APPROVALS,
        ("GET", f"/projects/{PID}/merge_requests"): lambda params: [{**MR, "state": "opened"}] if params.get("author_username") == "alice" else [],
    }
    h = host(routes)
    assert [m.iid for m in h.merge_requests_for_commit("abc123")] == [7]
    assert [m.state for m in h.list_open_by("alice")] == ["open"]
    assert h.list_open_by("bob") == []


def test_http_error_raises_githosterror():
    with pytest.raises(GitHostError, match="404"):
        host({}).merge_requests_for_commit("zzz")


def test_network_error_raises_githosterror():
    """Network failures are wrapped in GitHostError."""
    class FailingSession:
        def get(self, url, params=None, headers=None, timeout=None):
            raise requests.ConnectionError("boom")

        def post(self, url, json=None, headers=None, timeout=None):
            raise requests.ConnectionError("boom")

    h = GitLabHost("https://gitlab.bank", "tok", "pricing/team-knowledge", session=FailingSession())
    with pytest.raises(GitHostError, match="request failed"):
        h.merge_requests_for_commit("abc123")


def test_push_options_mode():
    h = host({}, mode="push-options")
    opts = h.push_options("[fact] T", "desc", ["bob", "carol"])
    assert opts == ["merge_request.create", "merge_request.target=main", "merge_request.title=[fact] T",
                    "merge_request.assign=bob", "merge_request.assign=carol"]
    assert host({}).push_options("t", "d", ["bob"]) == []


def test_push_options_open_polls_branch():
    routes = {
        ("GET", f"/projects/{PID}/merge_requests"): [{**MR, "state": "opened"}],
        ("GET", f"/projects/{PID}/merge_requests/7/approvals"): {"approved_by": []},
    }
    mr = host(routes, mode="push-options").open_merge_request("finding/01J9", "t", "d", [])
    assert mr.iid == 7
    with pytest.raises(GitHostError, match="did not create"):
        host({("GET", f"/projects/{PID}/merge_requests"): []}, mode="push-options").open_merge_request("b", "t", "d", [])


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("GITLAB_TOKEN"), reason="GITLAB_TOKEN not set")
def test_live_list_open():
    h = GitLabHost(os.environ.get("GITLAB_URL", "https://gitlab.com"), os.environ["GITLAB_TOKEN"], os.environ["GITLAB_PROJECT"])
    assert isinstance(h.list_open_by(os.environ.get("TK_AUTHOR", "")), list)
