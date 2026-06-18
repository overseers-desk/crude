"""crude-airwallex transport: header injection, pagination, retries — no network.

The session's requests.Session.request is monkeypatched, so nothing reaches the
network; each test drives one or more fake responses through AirwallexSession.
Mirrors the fake-response style of tests/test_xero.py.
"""

from __future__ import annotations

import time

import pytest

from crude_airwallex import auth
from crude_airwallex.client import AirwallexError, AirwallexSession, _items


class _FakeResp:
    def __init__(self, status=200, body=None, headers=None, content=True):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._body = {} if body is None else body
        self.headers = headers or {}
        self.content = b"x" if content else b""

    def json(self):
        return self._body


def _session(**kw):
    """A session with a non-expired token so _ensure_token never logs in."""
    return AirwallexSession(
        "acct", "cid", "key", base=auth.PROD_BASE,
        token={"token": "TOK", "expires_at": time.time() + 9999}, **kw,
    )


def test_bearer_header_injected_no_on_behalf_of(monkeypatch):
    xs = _session()
    seen = {}

    def fake(method, url, **kw):
        seen.update(headers=kw.get("headers"))
        return _FakeResp(body={"items": []})

    monkeypatch.setattr(xs.session, "request", fake)
    xs._get("/api/v1/financial_transactions")
    assert seen["headers"]["Authorization"] == "Bearer TOK"
    assert "x-on-behalf-of" not in seen["headers"]


def test_on_behalf_of_header_present_when_set(monkeypatch):
    xs = _session(on_behalf_of="acc_123")
    seen = {}

    def fake(method, url, **kw):
        seen.update(headers=kw.get("headers"))
        return _FakeResp(body={})

    monkeypatch.setattr(xs.session, "request", fake)
    xs._get("/x")
    assert seen["headers"]["x-on-behalf-of"] == "acc_123"


def test_items_unwrap():
    assert _items({"items": [1, 2], "has_more": False}) == [1, 2]
    assert _items([1, 2]) == [1, 2]
    assert _items({"foo": "bar"}) == []
    assert _items("nope") == []


def test_paginate_walks_pages_and_stops_on_has_more_false(monkeypatch):
    xs = _session()
    pages = [
        {"items": [{"id": 1}, {"id": 2}], "has_more": True},
        {"items": [{"id": 3}], "has_more": False},
    ]
    seen_pages = []

    def fake(method, url, **kw):
        seen_pages.append(kw["params"]["page_num"])
        return _FakeResp(body=pages[len(seen_pages) - 1])

    monkeypatch.setattr(xs.session, "request", fake)
    out = xs.paginate("/x")
    assert [r["id"] for r in out] == [1, 2, 3]
    assert seen_pages == [0, 1]  # 0-based page_num


def test_paginate_first_page_only_when_not_all_pages(monkeypatch):
    xs = _session()
    calls = {"n": 0}

    def fake(method, url, **kw):
        calls["n"] += 1
        return _FakeResp(body={"items": [{"id": 1}], "has_more": True})

    monkeypatch.setattr(xs.session, "request", fake)
    out = xs.paginate("/x", all_pages=False)
    assert len(out) == 1
    assert calls["n"] == 1


def test_paginate_honours_limit(monkeypatch):
    xs = _session()

    def fake(method, url, **kw):
        return _FakeResp(body={"items": [{"id": 1}, {"id": 2}, {"id": 3}], "has_more": True})

    monkeypatch.setattr(xs.session, "request", fake)
    out = xs.paginate("/x", limit=2)
    assert len(out) == 2


def test_paginate_cursor_follows_page_after(monkeypatch):
    xs = _session()
    pages = [
        {"items": [{"id": 1}], "has_more": True, "page_after": "cur1"},
        {"items": [{"id": 2}], "has_more": False},
    ]
    seen_after = []

    def fake(method, url, **kw):
        seen_after.append(kw["params"].get("page_after"))
        return _FakeResp(body=pages[len(seen_after) - 1])

    monkeypatch.setattr(xs.session, "request", fake)
    out = xs.paginate_cursor("/x")
    assert [r["id"] for r in out] == [1, 2]
    assert seen_after == [None, "cur1"]  # second request carried the cursor


def test_401_relogins_once_and_retries_with_new_token(monkeypatch):
    xs = _session()
    responses = [
        _FakeResp(status=401, body={"message": "expired"}),
        _FakeResp(status=200, body={"items": [{"id": 9}]}),
    ]
    seen_auth = []

    def fake(method, url, **kw):
        seen_auth.append(kw["headers"]["Authorization"])
        return responses[len(seen_auth) - 1]

    monkeypatch.setattr(xs.session, "request", fake)
    logins = {"n": 0}

    def fake_login():
        logins["n"] += 1
        xs.token = {"token": "TOK2", "expires_at": time.time() + 9999}

    monkeypatch.setattr(xs, "_login", fake_login)
    data = xs._get("/x")
    assert logins["n"] == 1
    assert _items(data) == [{"id": 9}]
    assert seen_auth == ["Bearer TOK", "Bearer TOK2"]  # retried with the fresh token


def test_429_backs_off_then_retries(monkeypatch):
    xs = _session()
    responses = [
        _FakeResp(status=429, headers={"Retry-After": "1"}),
        _FakeResp(status=200, body={"ok": True}),
    ]
    calls = {"n": 0}

    def fake(method, url, **kw):
        calls["n"] += 1
        return responses[calls["n"] - 1]

    monkeypatch.setattr(xs.session, "request", fake)
    monkeypatch.setattr("crude_common.httpapi.time.sleep", lambda s: None)
    assert xs._get("/x") == {"ok": True}
    assert calls["n"] == 2


def test_error_body_message_is_surfaced(monkeypatch):
    xs = _session()
    monkeypatch.setattr(
        xs.session, "request",
        lambda *a, **k: _FakeResp(status=400, body={"code": "invalid", "message": "bad request"}),
    )
    with pytest.raises(AirwallexError) as exc:
        xs._get("/x")
    assert "bad request" in str(exc.value)
    assert exc.value.status == 400
    assert exc.value.code == "invalid"


def test_write_verbs_use_their_http_method(monkeypatch):
    xs = _session()
    seen = {}

    def fake(method, url, **kw):
        seen.update(method=method, json=kw.get("json"))
        return _FakeResp(body={})

    monkeypatch.setattr(xs.session, "request", fake)
    xs._post("/x", json={"a": 1})
    assert seen["method"] == "POST" and seen["json"] == {"a": 1}
    xs._delete("/x/1")
    assert seen["method"] == "DELETE"
