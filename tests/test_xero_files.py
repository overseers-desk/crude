"""Unit tests for the crude-xero Files (files.xro/1.0) method group — no network.

These pin the behaviours the Files client hinges on: the `Items`-key list unwrap
(the Files product's own envelope, distinct from Accounting's single-plural-key
shape), the paged file list, a single-file get, the multipart/form-data upload
path (the one verb a JSON body cannot carry, so it must NOT send JSON and must hit
`Files`), the bare-array folder list, the raw-byte content download, a delete, and
the association add/remove path. The inner `requests.Session` is monkeypatched, so
nothing reaches the network.
"""

from __future__ import annotations

import time

from crude_xero.client import XeroSession
from crude_xero.files import FilesAPI, _items


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
# _items — the Items-key unwrap (NOT Accounting's plural-key logic)
# ----------------------------------------------------------------------


def test_items_unwraps_the_items_key():
    data = {"TotalCount": 2, "Page": 1, "PerPage": 100,
            "Items": [{"Id": "f1"}, {"Id": "f2"}]}
    assert _items(data) == [{"Id": "f1"}, {"Id": "f2"}]


def test_items_tolerates_odd_shapes():
    assert _items([{"Id": "f1"}]) == [{"Id": "f1"}]   # already a list (folders/assocs)
    assert _items({"TotalCount": 0}) == []            # no Items key
    assert _items("nope") == []                        # not dict/list
    assert _items({"Items": "x"}) == []                # Items not a list


# ----------------------------------------------------------------------
# Files — list (Items envelope, paged), get
# ----------------------------------------------------------------------


def test_list_files_unwraps_items_and_pages(monkeypatch):
    xs = _session()
    api = FilesAPI(xs)
    payload = {"TotalCount": 1, "Page": 1, "PerPage": 100, "Items": [{"Id": "f1"}]}
    calls, fake = _recorder(_FakeResp(payload))
    monkeypatch.setattr(xs.session, "request", fake)

    out = api.list_files()

    assert out == [{"Id": "f1"}]
    assert len(calls) == 1                              # short page -> one call
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/files.xro/1.0/Files")
    assert calls[0]["params"] == {"pagesize": 100, "page": 1}


def test_get_file_routes_to_path(monkeypatch):
    xs = _session()
    api = FilesAPI(xs)
    calls, fake = _recorder(_FakeResp({"Id": "f1", "Name": "receipt.pdf"}))
    monkeypatch.setattr(xs.session, "request", fake)

    out = api.get_file("f1")

    assert out == {"Id": "f1", "Name": "receipt.pdf"}
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/files.xro/1.0/Files/f1")


# ----------------------------------------------------------------------
# Upload — multipart/form-data, NOT JSON, hitting Files
# ----------------------------------------------------------------------


def test_upload_file_is_multipart_not_json(monkeypatch):
    xs = _session()
    api = FilesAPI(xs)
    calls, fake = _recorder(_FakeResp({"Id": "f1", "Name": "receipt.pdf"}))
    monkeypatch.setattr(xs.session, "request", fake)

    api.upload_file("receipt.pdf", b"PDFBYTES", "application/pdf")

    call = calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/files.xro/1.0/Files")   # root, no folder
    assert call["json"] is None                            # NOT a JSON body
    assert call["data"]                                    # a multipart byte body
    assert b"PDFBYTES" in call["data"]
    assert call["headers"]["Content-Type"].startswith("multipart/form-data")


def test_upload_file_targets_folder(monkeypatch):
    xs = _session()
    api = FilesAPI(xs)
    calls, fake = _recorder(_FakeResp({"Id": "f1"}))
    monkeypatch.setattr(xs.session, "request", fake)

    api.upload_file("receipt.pdf", b"PDFBYTES", "application/pdf", folder_id="FOLDER-1")

    assert calls[0]["url"].endswith("/files.xro/1.0/Files/FOLDER-1")
    assert calls[0]["json"] is None


# ----------------------------------------------------------------------
# Delete
# ----------------------------------------------------------------------


def test_delete_file_is_a_delete(monkeypatch):
    xs = _session()
    api = FilesAPI(xs)
    calls, fake = _recorder(_FakeResp(status_code=204))
    monkeypatch.setattr(xs.session, "request", fake)

    api.delete_file("f1")

    assert calls[0]["method"] == "DELETE"
    assert calls[0]["url"].endswith("/files.xro/1.0/Files/f1")


# ----------------------------------------------------------------------
# Folders — bare-array list; content — raw bytes
# ----------------------------------------------------------------------


def test_list_folders_returns_bare_array(monkeypatch):
    xs = _session()
    api = FilesAPI(xs)
    calls, fake = _recorder(_FakeResp([{"Id": "d1"}, {"Id": "d2"}]))
    monkeypatch.setattr(xs.session, "request", fake)

    out = api.list_folders()

    assert out == [{"Id": "d1"}, {"Id": "d2"}]
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/files.xro/1.0/Folders")


def test_get_file_content_returns_raw_bytes(monkeypatch):
    xs = _session()
    api = FilesAPI(xs)
    calls, fake = _recorder(_FakeResp(content=b"PDFBYTES"))
    monkeypatch.setattr(xs.session, "request", fake)

    out = api.get_file_content("f1")

    assert out == b"PDFBYTES"                              # raw bytes, not parsed JSON
    assert calls[0]["url"].endswith("/files.xro/1.0/Files/f1/Content")
    assert calls[0]["headers"]["Accept"] == "application/octet-stream"


# ----------------------------------------------------------------------
# Associations — add (POST under the file), remove (DELETE)
# ----------------------------------------------------------------------


def test_create_association_posts_under_the_file(monkeypatch):
    xs = _session()
    api = FilesAPI(xs)
    calls, fake = _recorder(_FakeResp({"FileId": "f1", "ObjectId": "o1"}))
    monkeypatch.setattr(xs.session, "request", fake)

    body = {"ObjectId": "o1", "ObjectType": "Invoice", "ObjectGroup": "Invoice"}
    api.create_association("f1", body)

    assert calls[0]["method"] == "POST"
    assert calls[0]["url"].endswith("/files.xro/1.0/Files/f1/Associations")
    assert calls[0]["json"] == body


def test_delete_association_routes_to_path(monkeypatch):
    xs = _session()
    api = FilesAPI(xs)
    calls, fake = _recorder(_FakeResp(status_code=204))
    monkeypatch.setattr(xs.session, "request", fake)

    api.delete_association("f1", "o1")

    assert calls[0]["method"] == "DELETE"
    assert calls[0]["url"].endswith("/files.xro/1.0/Files/f1/Associations/o1")
