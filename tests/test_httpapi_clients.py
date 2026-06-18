"""The crude_common.httpapi.HttpSession contract as seen through its subclasses.

The four homogeneous clients share one request/retry path; these lock the per-site
hooks that path calls — clover's narrower 429 bound and CloverError, deputy's error
shape and no-refresh, atdw's 401 refresh-and-retry. The session's request is
monkeypatched, so nothing reaches the network.
"""

from __future__ import annotations

import pytest

from crude_atdw.client import ATDWClient
from crude_clover.client import CloverError, CloverSession
from crude_deputy.client import DeputyClient


class _Req:
    def __init__(self, method="GET", path_url="/x"):
        self.method = method
        self.path_url = path_url


class _FakeResp:
    def __init__(self, status=200, body=None, headers=None, content=True):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._body = {} if body is None else body
        self.headers = headers or {}
        self.content = b"x" if content else b""
        self.text = "err"
        self.request = _Req()

    def json(self):
        return self._body


def _drive(monkeypatch, client, responses):
    """Feed `responses` to client.session.request in order; return call count box."""
    calls = {"n": 0}

    def fake(method, url, **kw):
        calls["n"] += 1
        return responses[calls["n"] - 1]

    monkeypatch.setattr(client.session, "request", fake)
    return calls


def test_clover_429_uses_clover_bound_not_the_base_default(monkeypatch):
    cs = CloverSession("tok")
    calls = _drive(monkeypatch, cs, [
        _FakeResp(429, headers={"Retry-After": "100"}),
        _FakeResp(200, body={"ok": True}),
    ])
    slept = {}
    monkeypatch.setattr("crude_common.httpapi.time.sleep", lambda s: slept.setdefault("s", s))
    assert cs.get("/x") == {"ok": True}
    assert calls["n"] == 2
    assert slept["s"] == 30  # clover's cap, not HttpSession's default 60


def test_clover_401_raises_cloverror_without_refreshing(monkeypatch):
    cs = CloverSession("tok")
    calls = _drive(monkeypatch, cs, [_FakeResp(401)])
    with pytest.raises(CloverError) as exc:
        cs.get("/x")
    assert exc.value.status == 401
    assert calls["n"] == 1  # no retry: clover does not refresh


def test_deputy_surfaces_error_message(monkeypatch):
    dc = DeputyClient("tok", "inst", "au")
    _drive(monkeypatch, dc, [_FakeResp(400, body={"error": {"message": "bad object"}})])
    with pytest.raises(RuntimeError) as exc:
        dc._get("/x")
    assert "bad object" in str(exc.value)


def test_deputy_401_raises_without_refreshing(monkeypatch):
    dc = DeputyClient("tok", "inst", "au")
    calls = _drive(monkeypatch, dc, [_FakeResp(401, body={"error": {"message": "nope"}})])
    with pytest.raises(RuntimeError):
        dc._get("/x")
    assert calls["n"] == 1  # default _on_401 is no-op, so one call


def test_atdw_401_refreshes_and_retries_once(monkeypatch):
    ac = ATDWClient("tok", credentials={"username": "u", "password": "p"})
    calls = _drive(monkeypatch, ac, [_FakeResp(401), _FakeResp(200, body={"id": 1})])
    monkeypatch.setattr(ac, "_try_refresh", lambda: True)
    assert ac._get("/x") == {"id": 1}
    assert calls["n"] == 2
