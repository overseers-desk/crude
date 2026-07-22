"""Unit tests for the crude-xero BankFeeds (bankfeeds.xro/1.0) method group — no network.

These pin the behaviours the BankFeeds client hinges on: the `{pagination, items}`
list-envelope unwrap (distinct from Accounting's single-plural-key shape), the
`page`/`pageSize` query params on the lists (unset filters dropped), the
items-wrapped create bodies, and — the unusual one — feed-connection deletion done
by POSTing a delete-request batch to `FeedConnections/DeleteRequests` rather than
an HTTP DELETE. The POST method and `bankfeeds.xro/1.0` base path are load-bearing.
The inner `requests.Session` is monkeypatched, so nothing reaches the network.
"""

from __future__ import annotations

import time

from crude_xero.bankfeeds import BankFeedsAPI
from crude_xero.client import XeroSession


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
        "acct", "client-id",
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


# ----------------------------------------------------------------------
# _items — the {pagination, items} unwrap (NOT Accounting's plural-key logic)
# ----------------------------------------------------------------------


def test_items_unwraps_the_items_key():
    data = {"pagination": {"page": 1, "itemCount": 2},
            "items": [{"id": "fc1"}, {"id": "fc2"}]}
    assert BankFeedsAPI._items(data) == [{"id": "fc1"}, {"id": "fc2"}]


def test_items_tolerates_odd_shapes():
    assert BankFeedsAPI._items([{"id": "fc1"}]) == [{"id": "fc1"}]  # already a list
    assert BankFeedsAPI._items({"pagination": {}}) == []           # no items key
    assert BankFeedsAPI._items("nope") == []                       # not dict/list
    assert BankFeedsAPI._items({"items": "x"}) == []               # items not a list


# ----------------------------------------------------------------------
# Feed connections — list (paged), get, create (items-wrapped)
# ----------------------------------------------------------------------


def test_list_feed_connections_unwraps_items(monkeypatch):
    xs = _session()
    api = BankFeedsAPI(xs)
    payload = {"pagination": {"page": 1}, "items": [{"id": "fc1"}]}
    calls, fake = _recorder(_FakeResp(payload))
    monkeypatch.setattr(xs.session, "request", fake)

    out = api.list_feed_connections()

    assert out == [{"id": "fc1"}]
    assert len(calls) == 1
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/bankfeeds.xro/1.0/FeedConnections")
    assert calls[0]["params"] is None  # nothing set, so no query params


def test_list_feed_connections_passes_paging(monkeypatch):
    xs = _session()
    api = BankFeedsAPI(xs)
    calls, fake = _recorder(_FakeResp({"items": []}))
    monkeypatch.setattr(xs.session, "request", fake)

    api.list_feed_connections(page=2, page_size=50)

    assert calls[0]["params"] == {"page": 2, "pageSize": 50}


def test_get_feed_connection_routes_to_path(monkeypatch):
    xs = _session()
    api = BankFeedsAPI(xs)
    calls, fake = _recorder(_FakeResp({"id": "fc1"}))
    monkeypatch.setattr(xs.session, "request", fake)

    out = api.get_feed_connection("fc1")

    assert out == {"id": "fc1"}
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/bankfeeds.xro/1.0/FeedConnections/fc1")


def test_create_feed_connections_is_a_post_of_an_items_batch(monkeypatch):
    xs = _session()
    api = BankFeedsAPI(xs)
    calls, fake = _recorder(_FakeResp({"items": [{"id": "fc1", "status": "PENDING"}]}))
    monkeypatch.setattr(xs.session, "request", fake)

    body = {"items": [{"accountToken": "tok", "accountNumber": "12345"}]}
    api.create_feed_connections(body)

    assert calls[0]["method"] == "POST"  # not PUT
    assert calls[0]["url"].endswith("/bankfeeds.xro/1.0/FeedConnections")
    assert calls[0]["json"] == body


# ----------------------------------------------------------------------
# Feed connections — delete via POST of a delete-request batch
# ----------------------------------------------------------------------


def test_delete_feed_connections_posts_to_delete_requests(monkeypatch):
    xs = _session()
    api = BankFeedsAPI(xs)
    calls, fake = _recorder(_FakeResp({"items": [{"id": "fc1", "status": "DELETED"}]}))
    monkeypatch.setattr(xs.session, "request", fake)

    body = {"items": [{"id": "fc1"}]}
    api.delete_feed_connections(body)

    # BankFeeds deletes by POSTing a delete-request batch, never HTTP DELETE.
    assert calls[0]["method"] == "POST"
    assert calls[0]["method"] != "DELETE"
    assert calls[0]["url"].endswith("/bankfeeds.xro/1.0/FeedConnections/DeleteRequests")
    assert calls[0]["json"] == body


# ----------------------------------------------------------------------
# Statements — list (paged), get, create (items-wrapped)
# ----------------------------------------------------------------------


def test_list_statements_unwraps_items_and_drops_unset(monkeypatch):
    xs = _session()
    api = BankFeedsAPI(xs)
    payload = {"pagination": {"page": 1}, "items": [{"id": "s1"}]}
    calls, fake = _recorder(_FakeResp(payload))
    monkeypatch.setattr(xs.session, "request", fake)

    out = api.list_statements()

    assert out == [{"id": "s1"}]
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/bankfeeds.xro/1.0/Statements")
    assert calls[0]["params"] is None


def test_get_statement_routes_to_path(monkeypatch):
    xs = _session()
    api = BankFeedsAPI(xs)
    calls, fake = _recorder(_FakeResp({"id": "s1"}))
    monkeypatch.setattr(xs.session, "request", fake)

    out = api.get_statement("s1")

    assert out == {"id": "s1"}
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/bankfeeds.xro/1.0/Statements/s1")


def test_create_statements_is_a_post_of_an_items_batch(monkeypatch):
    xs = _session()
    api = BankFeedsAPI(xs)
    calls, fake = _recorder(_FakeResp({"items": [{"id": "s1", "status": "PENDING"}]}))
    monkeypatch.setattr(xs.session, "request", fake)

    body = {"items": [{"feedConnectionId": "fc1", "startDate": "2026-06-01"}]}
    api.create_statements(body)

    assert calls[0]["method"] == "POST"
    assert calls[0]["url"].endswith("/bankfeeds.xro/1.0/Statements")
    assert calls[0]["json"] == body
