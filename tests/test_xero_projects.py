"""Unit tests for the crude-xero Projects method group — no network.

These pin the behaviours where the Projects product departs from Accounting: the
`{"pagination", "items"}` envelope unwrap (not Accounting's single-list-key one), a
single GET returning the bare object, the `page`/`pageSize` (plus `states`/
`contactID`) query params on a list, and the verbs whose HTTP method is
load-bearing — POST create, PUT task/time updates, DELETE removals, and above all
the PATCH used for a project status change. The inner `requests.Session` is
monkeypatched, so nothing reaches the network.
"""

from __future__ import annotations

import time

import pytest

from crude_xero.client import XeroSession
from crude_xero.projects import ProjectsAPI, _items


class _FakeResp:
    """Stands in for a requests.Response: just the attributes _request reads."""

    def __init__(self, payload=None, *, status_code=200, headers=None, content=None):
        self._payload = payload
        self.status_code = status_code
        self.ok = 200 <= status_code < 400
        self.headers = headers or {}
        if content is not None:
            self.content = content
        elif payload is None:
            self.content = b""
        else:
            self.content = b"<body>"  # truthy, so _request calls .json()

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


def _session(tenant_id="TENANT-1"):
    """A session with a far-future token, so _ensure_token never refreshes."""
    return XeroSession(
        "acct", "client-id", "client-secret",
        {"access_token": "ACCESS-1", "expires_at": time.time() + 9999},
        tenant_id=tenant_id,
    )


def _recorder(response):
    """Capture every transport call; return `response` (or response(call) if callable)."""
    calls = []

    def fake(method, url, params=None, json=None, data=None, headers=None):
        call = {"method": method, "url": url, "params": params,
                "json": json, "data": data, "headers": headers}
        calls.append(call)
        return response(call) if callable(response) else response

    return calls, fake


def _api(monkeypatch, response):
    """A ProjectsAPI whose transport is the recorder over `response`."""
    xs = _session()
    api = ProjectsAPI(xs)
    calls, fake = _recorder(response)
    monkeypatch.setattr(xs.session, "request", fake)
    return api, calls


# ----------------------------------------------------------------------
# _items — the {"pagination", "items"} unwrap (not Accounting's heuristic)
# ----------------------------------------------------------------------


def test_items_unwraps_the_items_key():
    data = {"pagination": {"page": 1, "pageSize": 50, "pageCount": 1, "itemCount": 2},
            "items": [{"projectId": "p1"}, {"projectId": "p2"}]}
    assert _items(data) == [{"projectId": "p1"}, {"projectId": "p2"}]


def test_items_tolerates_odd_shapes():
    assert _items([1, 2, 3]) == [1, 2, 3]          # already a list
    assert _items("nope") == []                    # not dict/list
    assert _items({"pagination": {}}) == []        # no items key
    assert _items({"items": None}) == []           # items present but not a list


# ----------------------------------------------------------------------
# Projects — list params, bare-object get, POST create, PATCH update
# ----------------------------------------------------------------------


def test_list_projects_pages_and_filters_then_unwraps(monkeypatch):
    payload = {"pagination": {"page": 1}, "items": [{"projectId": "p1"}]}
    api, calls = _api(monkeypatch, _FakeResp(payload))

    out = api.list_projects(page=2, page_size=50, states="INPROGRESS", contact_id="C1")

    assert out == [{"projectId": "p1"}]
    assert len(calls) == 1
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/projects.xro/2.0/Projects")
    # page/pageSize plus the camelCase filters; contact_id -> contactID.
    assert calls[0]["params"] == {
        "page": 2, "pageSize": 50, "states": "INPROGRESS", "contactID": "C1"}


def test_list_projects_drops_unset_params(monkeypatch):
    api, calls = _api(monkeypatch, _FakeResp({"items": []}))

    api.list_projects()

    assert calls[0]["params"] is None  # nothing set -> no query string


def test_get_project_returns_the_bare_object(monkeypatch):
    # Projects returns the single object directly, not list-wrapped like Accounting.
    api, calls = _api(monkeypatch, _FakeResp({"projectId": "p1", "name": "Build"}))

    out = api.get_project("p1")

    assert out == {"projectId": "p1", "name": "Build"}
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/projects.xro/2.0/Projects/p1")


def test_create_project_is_a_post(monkeypatch):
    api, calls = _api(monkeypatch, _FakeResp({"projectId": "p1"}))

    api.create_project({"contactId": "C1", "name": "Build"})

    assert calls[0]["method"] == "POST"
    assert calls[0]["url"].endswith("/projects.xro/2.0/Projects")
    assert calls[0]["json"] == {"contactId": "C1", "name": "Build"}


def test_update_project_is_a_patch(monkeypatch):
    # The load-bearing one: a project status change is a PATCH, not POST/PUT.
    api, calls = _api(monkeypatch, _FakeResp(None, status_code=204))

    api.update_project("p1", {"status": "CLOSED"})

    assert len(calls) == 1
    assert calls[0]["method"] == "PATCH"
    assert calls[0]["url"].endswith("/projects.xro/2.0/Projects/p1")
    assert calls[0]["json"] == {"status": "CLOSED"}


# ----------------------------------------------------------------------
# Tasks — nested path, and the create/update/delete verb methods
# ----------------------------------------------------------------------


def test_list_tasks_scopes_to_the_project(monkeypatch):
    api, calls = _api(monkeypatch, _FakeResp({"items": [{"taskId": "t1"}]}))

    out = api.list_tasks("p1", page=1, page_size=10)

    assert out == [{"taskId": "t1"}]
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/projects.xro/2.0/Projects/p1/Tasks")
    assert calls[0]["params"] == {"page": 1, "pageSize": 10}


@pytest.mark.parametrize("call, method, suffix, body", [
    (lambda a: a.get_task("p1", "t1"), "GET", "Projects/p1/Tasks/t1", None),
    (lambda a: a.create_task("p1", {"name": "x"}), "POST", "Projects/p1/Tasks", {"name": "x"}),
    (lambda a: a.update_task("p1", "t1", {"name": "x"}), "PUT", "Projects/p1/Tasks/t1", {"name": "x"}),
    (lambda a: a.delete_task("p1", "t1"), "DELETE", "Projects/p1/Tasks/t1", None),
])
def test_task_verbs_use_the_right_method_and_path(monkeypatch, call, method, suffix, body):
    api, calls = _api(monkeypatch, _FakeResp({"taskId": "t1"}))

    call(api)

    assert calls[0]["method"] == method
    assert calls[0]["url"].endswith(f"/projects.xro/2.0/{suffix}")
    assert calls[0]["json"] == body


# ----------------------------------------------------------------------
# Time entries — same nested shape, PUT update, DELETE remove
# ----------------------------------------------------------------------


@pytest.mark.parametrize("call, method, suffix, body", [
    (lambda a: a.get_time("p1", "e1"), "GET", "Projects/p1/Time/e1", None),
    (lambda a: a.create_time("p1", {"duration": 60}), "POST", "Projects/p1/Time", {"duration": 60}),
    (lambda a: a.update_time("p1", "e1", {"duration": 90}), "PUT", "Projects/p1/Time/e1", {"duration": 90}),
    (lambda a: a.delete_time("p1", "e1"), "DELETE", "Projects/p1/Time/e1", None),
])
def test_time_verbs_use_the_right_method_and_path(monkeypatch, call, method, suffix, body):
    api, calls = _api(monkeypatch, _FakeResp({"timeEntryId": "e1"}))

    call(api)

    assert calls[0]["method"] == method
    assert calls[0]["url"].endswith(f"/projects.xro/2.0/{suffix}")
    assert calls[0]["json"] == body


def test_list_time_scopes_to_the_project(monkeypatch):
    api, calls = _api(monkeypatch, _FakeResp({"items": [{"timeEntryId": "e1"}]}))

    out = api.list_time("p1")

    assert out == [{"timeEntryId": "e1"}]
    assert calls[0]["url"].endswith("/projects.xro/2.0/Projects/p1/Time")


# ----------------------------------------------------------------------
# Project users — read-only list off the product root
# ----------------------------------------------------------------------


def test_list_project_users_unwraps_items(monkeypatch):
    payload = {"pagination": {"page": 1}, "items": [{"userId": "u1"}, {"userId": "u2"}]}
    api, calls = _api(monkeypatch, _FakeResp(payload))

    out = api.list_project_users()

    assert out == [{"userId": "u1"}, {"userId": "u2"}]
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/projects.xro/2.0/ProjectUsers")
