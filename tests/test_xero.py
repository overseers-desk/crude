"""Unit tests for the crude-xero transport and Accounting method groups — no network.

These pin the behaviours the Xero client hinges on: the Bearer + tenant header
injection (and the tenant-less `connections` call), the single-list-key payload
unwrap, the `page`-param pagination that stops on a short page, the 401
refresh-then-retry, Xero's two error-message shapes, and the Accounting verbs
whose HTTP method is load-bearing (status-POST soft-deletes, raw-byte attachment
PUTs). The inner `requests.Session` is monkeypatched, so nothing reaches the
network. Also covers `crude_common.config.write_config`'s atomic round-trip.
"""

from __future__ import annotations

import time

import pytest

from crude_xero.accounting import REPORT_NAMES, AccountingAPI
from crude_xero.client import XeroError, XeroSession, _extract_list


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


# ----------------------------------------------------------------------
# _extract_list — the single-list-key unwrap (mirrors rezdy's _payload)
# ----------------------------------------------------------------------


def test_extract_list_returns_the_single_list_key():
    assert _extract_list({"Invoices": [1, 2], "Status": "OK", "Id": "x"}) == [1, 2]


def test_extract_list_tolerates_odd_shapes():
    assert _extract_list([1, 2, 3]) == [1, 2, 3]          # already a list
    assert _extract_list("nope") == []                    # not dict/list
    assert _extract_list({"Status": "OK"}) == []          # no list-valued key
    # Two list keys: the first non-empty one wins (envelope + data).
    assert _extract_list({"Empty": [], "Invoices": [9]}) == [9]


# ----------------------------------------------------------------------
# Header injection
# ----------------------------------------------------------------------


def test_get_injects_bearer_and_tenant_headers(monkeypatch):
    xs = _session(tenant_id="TENANT-1")
    calls, fake = _recorder(_FakeResp({"Status": "OK"}))
    monkeypatch.setattr(xs.session, "request", fake)

    xs._get("accounting", "Invoices")

    headers = calls[0]["headers"]
    assert headers["Authorization"] == "Bearer ACCESS-1"
    assert headers["xero-tenant-id"] == "TENANT-1"
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/api.xro/2.0/Invoices")


def test_connections_omits_tenant_header(monkeypatch):
    xs = _session(tenant_id="TENANT-1")
    captured = {}

    def fake_get(url, headers=None):
        captured["url"] = url
        captured["headers"] = headers
        return _FakeResp([{"tenantId": "T1"}])

    monkeypatch.setattr(xs.session, "get", fake_get)

    out = xs.connections()

    assert out == [{"tenantId": "T1"}]
    assert captured["headers"]["Authorization"] == "Bearer ACCESS-1"
    assert "xero-tenant-id" not in captured["headers"]


# ----------------------------------------------------------------------
# Pagination
# ----------------------------------------------------------------------


def test_paginate_walks_pages_and_stops_on_short_page(monkeypatch):
    xs = _session()
    pages = {
        1: {"Invoices": [{"n": 1}, {"n": 2}]},   # full page (== page_size)
        2: {"Invoices": [{"n": 3}]},              # short page -> stop
    }
    seen = []

    def fake_get(product, path, params=None):
        seen.append(params["page"])
        return pages[params["page"]]

    monkeypatch.setattr(xs, "_get", fake_get)

    out = xs.paginate("accounting", "Invoices", page_size=2)

    assert out == [{"n": 1}, {"n": 2}, {"n": 3}]
    assert seen == [1, 2]  # walked page 1 then 2, then stopped


def test_paginate_fetches_only_the_first_page_when_not_all(monkeypatch):
    xs = _session()
    pages = {
        1: {"Invoices": [{"n": 1}, {"n": 2}]},   # full page (== page_size)
        2: {"Invoices": [{"n": 3}]},
    }
    seen = []

    def fake_get(product, path, params=None):
        seen.append(params["page"])
        return pages[params["page"]]

    monkeypatch.setattr(xs, "_get", fake_get)

    out = xs.paginate("accounting", "Invoices", page_size=2, all_pages=False)

    assert out == [{"n": 1}, {"n": 2}]  # the full first page only
    assert seen == [1]                  # stopped despite the page being full


def test_paginate_limit_truncates_across_pages(monkeypatch):
    xs = _session()
    pages = {
        1: {"Invoices": [{"n": 1}, {"n": 2}]},
        2: {"Invoices": [{"n": 3}, {"n": 4}]},
        3: {"Invoices": [{"n": 5}]},
    }
    seen = []

    def fake_get(product, path, params=None):
        seen.append(params["page"])
        return pages[params["page"]]

    monkeypatch.setattr(xs, "_get", fake_get)

    out = xs.paginate("accounting", "Invoices", page_size=2, limit=3)

    assert out == [{"n": 1}, {"n": 2}, {"n": 3}]  # paged to reach 3, then truncated
    assert seen == [1, 2]                         # stopped once >= 3 collected


# ----------------------------------------------------------------------
# 401 refresh-then-retry, and the two error-message shapes
# ----------------------------------------------------------------------


def test_401_triggers_exactly_one_refresh_and_retry(monkeypatch):
    xs = _session()
    responses = [_FakeResp(status_code=401), _FakeResp({"Invoices": []})]

    headers_seen = []

    def fake(method, url, params=None, json=None, data=None, headers=None):
        headers_seen.append(headers)
        return responses.pop(0)

    monkeypatch.setattr(xs.session, "request", fake)

    refreshed = {"n": 0}

    def fake_refresh():
        refreshed["n"] += 1
        xs.tokens = {"access_token": "ACCESS-2", "expires_at": time.time() + 9999}

    monkeypatch.setattr(xs, "_refresh", fake_refresh)

    out = xs._get("accounting", "Invoices")

    assert out == {"Invoices": []}
    assert refreshed["n"] == 1                                  # exactly one refresh
    assert headers_seen[0]["Authorization"] == "Bearer ACCESS-1"
    assert headers_seen[1]["Authorization"] == "Bearer ACCESS-2"  # retry used new token
    assert not responses                                        # both responses consumed


def test_top_level_message_raises_xero_error(monkeypatch):
    xs = _session()
    body = {"Message": "The resource you have specified cannot be found."}
    _, fake = _recorder(_FakeResp(body, status_code=404))
    monkeypatch.setattr(xs.session, "request", fake)

    with pytest.raises(XeroError) as exc:
        xs._get("accounting", "Invoices/nope")
    assert "cannot be found" in str(exc.value)
    assert exc.value.status == 404


def test_validation_errors_surface_in_message(monkeypatch):
    xs = _session()
    body = {"Elements": [
        {"ValidationErrors": [
            {"Message": "Account code 'ZZZ' is not valid"},
            {"Message": "Contact is required"},
        ]}
    ]}
    _, fake = _recorder(_FakeResp(body, status_code=400))
    monkeypatch.setattr(xs.session, "request", fake)

    with pytest.raises(XeroError) as exc:
        xs._post("accounting", "Invoices", json={})
    msg = str(exc.value)
    assert "Account code 'ZZZ' is not valid" in msg
    assert "Contact is required" in msg


# ----------------------------------------------------------------------
# AccountingAPI — list/page, status-POST deletes, raw-byte attachments, reports
# ----------------------------------------------------------------------


def test_list_invoices_paginates_and_unwraps(monkeypatch):
    xs = _session()
    api = AccountingAPI(xs)
    # A short first page (1 < page_size 100) stops pagination after one call.
    calls, fake = _recorder(_FakeResp({"Invoices": [{"InvoiceID": "i1"}], "Status": "OK"}))
    monkeypatch.setattr(xs.session, "request", fake)

    out = api.list_invoices(where='Status=="AUTHORISED"')

    assert out == [{"InvoiceID": "i1"}]
    assert len(calls) == 1
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/api.xro/2.0/Invoices")
    assert calls[0]["params"] == {"where": 'Status=="AUTHORISED"', "page": 1}


def test_list_invoices_first_page_default_and_all_pages_walks(monkeypatch):
    xs = _session()
    api = AccountingAPI(xs)
    # Two full pages (== the default page_size) then a short one. A full first
    # page would page on were it not for the first-page default; --all keeps
    # walking until the short page.
    def invoices(lo, hi):
        return [{"InvoiceID": f"i{n}"} for n in range(lo, hi)]

    pages = {
        1: {"Invoices": invoices(0, 100)},
        2: {"Invoices": invoices(100, 200)},
        3: {"Invoices": invoices(200, 201)},
    }
    seen = []

    def fake_get(product, path, params=None):
        seen.append(params["page"])
        return pages[params["page"]]

    monkeypatch.setattr(xs, "_get", fake_get)

    default = api.list_invoices()                 # no flags -> first page only
    assert len(default) == 100
    assert seen == [1]

    seen.clear()
    everything = api.list_invoices(all_pages=True)  # --all -> walk to the short page
    assert len(everything) == 201
    assert seen == [1, 2, 3]

    seen.clear()
    capped = api.list_invoices(limit=150)         # --limit spans pages, then truncates
    assert len(capped) == 150
    assert seen == [1, 2]


@pytest.mark.parametrize("verb, collection, status", [
    ("delete_payment", "Payments", "DELETED"),
    ("delete_batch_payment", "BatchPayments", "DELETED"),
    ("archive_contact", "Contacts", "ARCHIVED"),
])
def test_status_change_is_a_post_not_a_delete(monkeypatch, verb, collection, status):
    xs = _session()
    api = AccountingAPI(xs)
    calls, fake = _recorder(_FakeResp({"Status": "OK"}))
    monkeypatch.setattr(xs.session, "request", fake)

    getattr(api, verb)("GUID-1")

    assert len(calls) == 1
    assert calls[0]["method"] == "POST"  # not DELETE, not PUT
    assert calls[0]["url"].endswith(f"/api.xro/2.0/{collection}/GUID-1")
    assert calls[0]["json"] == {"Status": status}


def test_add_attachment_puts_raw_bytes_to_mapped_collection(monkeypatch):
    xs = _session()
    api = AccountingAPI(xs)
    calls, fake = _recorder(_FakeResp({"Attachments": []}))
    monkeypatch.setattr(xs.session, "request", fake)

    api.add_attachment("invoice", "GUID-1", "receipt.pdf", b"PDFBYTES", "application/pdf")

    call = calls[0]
    assert call["method"] == "PUT"
    # "invoice" maps via ATTACHMENT_ENDPOINTS to the "Invoices" collection.
    assert call["url"].endswith("/api.xro/2.0/Invoices/GUID-1/Attachments/receipt.pdf")
    assert call["data"] == b"PDFBYTES"       # raw bytes, not JSON
    assert call["json"] is None
    assert call["headers"]["Content-Type"] == "application/pdf"


def test_add_attachment_unknown_endpoint_raises():
    xs = _session()
    api = AccountingAPI(xs)
    # The method guards via ATTACHMENT_ENDPOINTS before any transport call.
    with pytest.raises(XeroError) as exc:
        api.add_attachment("frobnicate", "GUID-1", "f.pdf", b"x", "application/pdf")
    assert "frobnicate" in str(exc.value)


def test_get_report_routes_to_xero_report_path(monkeypatch):
    xs = _session()
    api = AccountingAPI(xs)
    calls, fake = _recorder(_FakeResp({"Reports": [{"ReportID": "BalanceSheet"}]}))
    monkeypatch.setattr(xs.session, "request", fake)

    # REPORT_NAMES carries the friendly->Xero-name mapping; get_report takes the
    # Xero name and routes it to Reports/<XeroName>.
    assert REPORT_NAMES["balance-sheet"] == "BalanceSheet"
    api.get_report(REPORT_NAMES["balance-sheet"])

    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/api.xro/2.0/Reports/BalanceSheet")


# ----------------------------------------------------------------------
# crude_common.config.write_config — atomic round-trip
# ----------------------------------------------------------------------


def test_write_config_round_trips(tmp_path):
    from crude_common.config import read_config, write_config

    cfg = {
        "xero": {
            "client_id": "CID",
            "tenant_id": "T1",
            "scopes": "openid accounting.transactions",
            "es": {"client_id": "CID-ES", "tenant_id": "T2"},
        },
        "rezdy": {"api_key": "K", "production": True},
    }
    path = tmp_path / "config.toml"
    write_config(path, cfg)

    assert read_config(path) == cfg
    # Atomic via temp-then-replace: no .tmp debris left in the directory.
    assert [p.name for p in tmp_path.iterdir()] == ["config.toml"]


def test_write_config_failure_does_not_corrupt_existing(tmp_path, monkeypatch):
    import tomli_w

    from crude_common.config import read_config, write_config

    path = tmp_path / "config.toml"
    write_config(path, {"xero": {"client_id": "ORIGINAL"}})

    def boom(config, f):
        raise RuntimeError("serialiser blew up mid-write")

    monkeypatch.setattr(tomli_w, "dump", boom)

    with pytest.raises(RuntimeError):
        write_config(path, {"xero": {"client_id": "NEW"}})

    # os.replace never ran, so the original survives intact...
    assert read_config(path) == {"xero": {"client_id": "ORIGINAL"}}
    # ...and the failed temp file was cleaned up.
    assert [p.name for p in tmp_path.iterdir()] == ["config.toml"]
