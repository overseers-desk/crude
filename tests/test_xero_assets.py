"""Unit tests for the crude-xero Assets (assets.xro/1.0) method group — no network.

These pin the behaviours the Assets client hinges on: the `{pagination, items}`
list-envelope unwrap (distinct from Accounting's single-plural-key shape), the
required `status` query param on the asset list (with unset filters dropped), the
bare-array asset-types list, the read-only settings object, and the create verbs
whose POST method and `assets.xro/1.0` base path are load-bearing. The inner
`requests.Session` is monkeypatched, so nothing reaches the network.
"""

from __future__ import annotations

import time

from crude_xero.assets import AssetsAPI
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
            "items": [{"assetId": "a1"}, {"assetId": "a2"}]}
    assert AssetsAPI._items(data) == [{"assetId": "a1"}, {"assetId": "a2"}]


def test_items_tolerates_odd_shapes():
    assert AssetsAPI._items([{"assetId": "a1"}]) == [{"assetId": "a1"}]  # already a list
    assert AssetsAPI._items({"pagination": {}}) == []                    # no items key
    assert AssetsAPI._items("nope") == []                                # not dict/list
    assert AssetsAPI._items({"items": "x"}) == []                        # items not a list


# ----------------------------------------------------------------------
# Assets — list (required status, dropped filters), get, create
# ----------------------------------------------------------------------


def test_list_assets_unwraps_items_and_sends_status(monkeypatch):
    xs = _session()
    api = AssetsAPI(xs)
    payload = {"pagination": {"page": 1}, "items": [{"assetId": "a1"}]}
    calls, fake = _recorder(_FakeResp(payload))
    monkeypatch.setattr(xs.session, "request", fake)

    out = api.list_assets("REGISTERED")

    assert out == [{"assetId": "a1"}]
    assert len(calls) == 1
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/assets.xro/1.0/Assets")
    assert calls[0]["params"] == {"status": "REGISTERED"}  # required filter; nothing else set


def test_list_assets_passes_paging_and_drops_unset(monkeypatch):
    xs = _session()
    api = AssetsAPI(xs)
    calls, fake = _recorder(_FakeResp({"items": []}))
    monkeypatch.setattr(xs.session, "request", fake)

    api.list_assets("DRAFT", page=2, page_size=50, order_by="assetName")

    assert calls[0]["params"] == {
        "status": "DRAFT", "page": 2, "pageSize": 50, "orderBy": "assetName"}


def test_get_asset_routes_to_path(monkeypatch):
    xs = _session()
    api = AssetsAPI(xs)
    calls, fake = _recorder(_FakeResp({"assetId": "a1"}))
    monkeypatch.setattr(xs.session, "request", fake)

    out = api.get_asset("a1")

    assert out == {"assetId": "a1"}
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/assets.xro/1.0/Assets/a1")


def test_create_asset_is_a_post(monkeypatch):
    xs = _session()
    api = AssetsAPI(xs)
    calls, fake = _recorder(_FakeResp({"assetId": "a1", "assetStatus": "DRAFT"}))
    monkeypatch.setattr(xs.session, "request", fake)

    api.create_asset({"assetName": "Laptop", "assetTypeId": "t1"})

    assert calls[0]["method"] == "POST"  # not PUT
    assert calls[0]["url"].endswith("/assets.xro/1.0/Assets")
    assert calls[0]["json"] == {"assetName": "Laptop", "assetTypeId": "t1"}


# ----------------------------------------------------------------------
# Asset types — bare-array list, create
# ----------------------------------------------------------------------


def test_list_asset_types_returns_bare_array(monkeypatch):
    xs = _session()
    api = AssetsAPI(xs)
    calls, fake = _recorder(_FakeResp([{"assetTypeId": "t1"}, {"assetTypeId": "t2"}]))
    monkeypatch.setattr(xs.session, "request", fake)

    out = api.list_asset_types()

    assert out == [{"assetTypeId": "t1"}, {"assetTypeId": "t2"}]
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/assets.xro/1.0/AssetTypes")


def test_create_asset_type_is_a_post(monkeypatch):
    xs = _session()
    api = AssetsAPI(xs)
    calls, fake = _recorder(_FakeResp({"assetTypeId": "t1"}))
    monkeypatch.setattr(xs.session, "request", fake)

    api.create_asset_type({"assetTypeName": "Computer Equipment"})

    assert calls[0]["method"] == "POST"
    assert calls[0]["url"].endswith("/assets.xro/1.0/AssetTypes")
    assert calls[0]["json"] == {"assetTypeName": "Computer Equipment"}


# ----------------------------------------------------------------------
# Settings — read-only singleton
# ----------------------------------------------------------------------


def test_get_settings_returns_single_object(monkeypatch):
    xs = _session()
    api = AssetsAPI(xs)
    calls, fake = _recorder(_FakeResp({"assetNumberPrefix": "FA-", "assetNumberSequence": "0001"}))
    monkeypatch.setattr(xs.session, "request", fake)

    out = api.get_settings()

    assert out == {"assetNumberPrefix": "FA-", "assetNumberSequence": "0001"}
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/assets.xro/1.0/Settings")
