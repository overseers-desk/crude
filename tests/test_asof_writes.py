"""Every write path refuses under WORLD_AS_OF and passes through when unset.

One test pair per write choke point, across both layers: the CLI gate
(writeio.do_write, covered in test_asof.py) and the client/transport gates this
file exercises (Sonas DDP method calls, Deputy resource writes, ATDW listing
writes, Rezdy's ``_write``, and the mutating transport verbs of Xero, Facebook,
Airwallex, and Clover). No test touches the network: each guard is asserted to
fire before any transport call, and the pass-through cases ride a monkeypatched
requests session (the repo's standard fake-response pattern).
"""

from __future__ import annotations

import time

import pytest

from crude_common import asof

BOUND = "2026-07-12T17:07:00+10:00"


@pytest.fixture
def bound(monkeypatch):
    monkeypatch.setenv(asof.ENV, BOUND)


@pytest.fixture
def unbound(monkeypatch):
    monkeypatch.delenv(asof.ENV, raising=False)


class _FakeResp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._body = {} if body is None else body
        self.headers = {}
        self.content = b"x"

    def json(self):
        return self._body


def _no_network(*a, **k):
    raise AssertionError("a refused write reached the network")


# ----------------------------------------------------------------------
# Sonas: DDP method calls (every method is a write path)
# ----------------------------------------------------------------------


def _sonas_client():
    from crude_sonas.client import SonasClient

    return SonasClient("user", "digest")


def test_sonas_call_refuses_before_any_session(bound, monkeypatch):
    client = _sonas_client()
    monkeypatch.setattr(client, "_ensure", _no_network)
    with pytest.raises(asof.WorldAsOfError):
        client.call("eventChangeStatus", {"eventId": "E1", "toStatus": 1})


def test_sonas_call_passes_when_unbound(unbound, monkeypatch):
    client = _sonas_client()
    monkeypatch.setattr(client, "_ensure", lambda: None)
    monkeypatch.setattr("crude_sonas.client.ddp_call", lambda conn, m, p: {"ok": True})
    assert client.call("eventChangeStatus", {"eventId": "E1"}) == {"ok": True}


# ----------------------------------------------------------------------
# Deputy: resource create/update/delete (not routed through do_write)
# ----------------------------------------------------------------------


def _deputy_client():
    from crude_deputy.client import DeputyClient

    return DeputyClient("token", "install", "au")


@pytest.mark.parametrize("verb,args", [
    ("create_resource", ("Roster", {"Date": "2026-07-20"})),
    ("update_resource", ("Roster", "5", {"Comment": "x"})),
    ("delete_resource", ("Roster", "5")),
])
def test_deputy_writes_refuse(bound, monkeypatch, verb, args):
    client = _deputy_client()
    monkeypatch.setattr(client.session, "request", _no_network)
    with pytest.raises(asof.WorldAsOfError):
        getattr(client, verb)(*args)


def test_deputy_write_passes_when_unbound(unbound, monkeypatch):
    client = _deputy_client()
    monkeypatch.setattr(client.session, "request",
                        lambda method, url, **kw: _FakeResp(body={"Id": 5}))
    assert client.create_resource("Roster", {"Date": "2026-07-20"}) == {"Id": 5}


def test_deputy_query_read_still_posts_under_bound(bound, monkeypatch):
    # Deputy's QUERY is a POST-shaped read; the write gate must not catch it.
    client = _deputy_client()
    monkeypatch.setattr(client.session, "request",
                        lambda method, url, **kw: _FakeResp(body=[{"Id": 1}]))
    assert client.query_resource("Roster") == [{"Id": 1}]


# ----------------------------------------------------------------------
# ATDW: listing writes (not routed through do_write)
# ----------------------------------------------------------------------


def _atdw_client():
    from crude_atdw.client import ATDWClient

    return ATDWClient("token")


@pytest.mark.parametrize("verb,args", [
    ("create_listing", ({"name": "x"},)),
    ("patch_listing", ("L1", {"description": "x"})),
    ("submit", ("L1",)),
    ("add_tag", ("L1", "T1")),
    ("remove_tag", ("L1", "T1")),
])
def test_atdw_writes_refuse(bound, monkeypatch, verb, args):
    client = _atdw_client()
    monkeypatch.setattr(client.session, "request", _no_network)
    with pytest.raises(asof.WorldAsOfError):
        getattr(client, verb)(*args)


def test_atdw_write_passes_when_unbound(unbound, monkeypatch):
    client = _atdw_client()
    monkeypatch.setattr(client.session, "request",
                        lambda method, url, **kw: _FakeResp(body={"id": "L1"}))
    assert client.patch_listing("L1", {"description": "x"}) == {"id": "L1"}


def test_atdw_search_post_still_reads_under_bound(bound, monkeypatch):
    # The all-visible search is a POST-shaped read; the write gate must not catch it.
    client = _atdw_client()
    monkeypatch.setattr(client.session, "request",
                        lambda method, url, **kw: _FakeResp(body=[{"id": "L1"}]))
    assert client.search_listings([{"status": "ACTIVE"}]) == [{"id": "L1"}]


# ----------------------------------------------------------------------
# Rezdy: every _write verb (and the now-valued quote riding it)
# ----------------------------------------------------------------------


def _rezdy_client():
    from crude_rezdy.client import RezdyClient

    return RezdyClient("key")


@pytest.mark.parametrize("call", [
    lambda c: c.create_booking({"status": "CONFIRMED"}),
    lambda c: c.update_product("P1", {"name": "x"}),
    lambda c: c.cancel_booking("R1"),
    lambda c: c.quote_booking({"items": []}),  # priced from the live present
    lambda c: c.set_order_checkin("P1", "R1"),
])
def test_rezdy_writes_refuse(bound, monkeypatch, call):
    client = _rezdy_client()
    monkeypatch.setattr(client.session, "request", _no_network)
    with pytest.raises(asof.WorldAsOfError):
        call(client)


def test_rezdy_write_passes_when_unbound(unbound, monkeypatch):
    client = _rezdy_client()
    monkeypatch.setattr(
        client.session, "request",
        lambda method, url, **kw: _FakeResp(body={"requestStatus": {"success": True},
                                                  "booking": {"orderNumber": "R1"}}))
    assert client.update_booking("R1", {}) == {"orderNumber": "R1"}


# ----------------------------------------------------------------------
# Xero: every non-GET transport verb across all seven products
# ----------------------------------------------------------------------


def _xero_session():
    from crude_xero.client import XeroSession

    return XeroSession("acct", "cid",
                       {"access_token": "T", "refresh_token": "R",
                        "expires_at": time.time() + 9999})


@pytest.mark.parametrize("call", [
    lambda s: s._post("accounting", "Invoices/G1", json={"Status": "VOIDED"}),
    lambda s: s._put("accounting", "Invoices", json={}),
    lambda s: s._delete("accounting", "Items/G1"),
    lambda s: s._put_raw("accounting", "Invoices/G1/Attachments/a.pdf",
                         data=b"x", content_type="application/pdf"),
    lambda s: s._post("payroll_au", "Employees", json={}),
])
def test_xero_writes_refuse(bound, monkeypatch, call):
    sess = _xero_session()
    monkeypatch.setattr(sess.session, "request", _no_network)
    with pytest.raises(asof.WorldAsOfError):
        call(sess)


def test_xero_get_still_reads_under_bound(bound, monkeypatch):
    sess = _xero_session()
    monkeypatch.setattr(sess.session, "request",
                        lambda method, url, **kw: _FakeResp(body={"Invoices": []}))
    assert sess._get("accounting", "Invoices") == {"Invoices": []}


def test_xero_write_passes_when_unbound(unbound, monkeypatch):
    sess = _xero_session()
    monkeypatch.setattr(sess.session, "request",
                        lambda method, url, **kw: _FakeResp(body={"Status": "OK"}))
    assert sess._post("accounting", "Invoices/G1", json={}) == {"Status": "OK"}


# ----------------------------------------------------------------------
# Facebook: the Graph write verbs (POST/DELETE)
# ----------------------------------------------------------------------


def _fb_session():
    from crude_facebook.client import FacebookSession

    sess = FacebookSession("TOK", page_id="P1")
    sess._page = {"id": "P1", "name": "Page", "access_token": "TOK"}
    return sess


def test_facebook_post_and_delete_refuse(bound, monkeypatch):
    sess = _fb_session()
    monkeypatch.setattr(sess.session, "request", _no_network)
    with pytest.raises(asof.WorldAsOfError):
        sess.post("/P1/feed", params={"message": "hi"})
    with pytest.raises(asof.WorldAsOfError):
        sess.delete("/POST1")


def test_facebook_get_still_reads_under_bound(bound, monkeypatch):
    sess = _fb_session()
    monkeypatch.setattr(sess.session, "request",
                        lambda method, url, **kw: _FakeResp(body={"data": []}))
    assert sess.get("/P1/published_posts") == {"data": []}


def test_facebook_post_passes_when_unbound(unbound, monkeypatch):
    sess = _fb_session()
    monkeypatch.setattr(sess.session, "request",
                        lambda method, url, **kw: _FakeResp(body={"id": "1_2"}))
    assert sess.post("/P1/feed", params={"message": "hi"}) == {"id": "1_2"}


# ----------------------------------------------------------------------
# Airwallex: every non-GET transport verb
# ----------------------------------------------------------------------


def _awx_session():
    from crude_airwallex import auth
    from crude_airwallex.client import AirwallexSession

    return AirwallexSession("acct", "cid", "key", base=auth.PROD_BASE,
                            token={"token": "TOK", "expires_at": time.time() + 9999})


def test_airwallex_writes_refuse(bound, monkeypatch):
    sess = _awx_session()
    monkeypatch.setattr(sess.session, "request", _no_network)
    with pytest.raises(asof.WorldAsOfError):
        sess._post("/api/v1/transfers/create", json={})
    with pytest.raises(asof.WorldAsOfError):
        sess._delete("/api/v1/pa/customers/delete/C1")


def test_airwallex_get_still_reads_under_bound(bound, monkeypatch):
    sess = _awx_session()
    monkeypatch.setattr(sess.session, "request",
                        lambda method, url, **kw: _FakeResp(body={"items": []}))
    assert sess._get("/api/v1/transfers") == {"items": []}


def test_airwallex_write_passes_when_unbound(unbound, monkeypatch):
    sess = _awx_session()
    monkeypatch.setattr(sess.session, "request",
                        lambda method, url, **kw: _FakeResp(body={"id": "t1"}))
    assert sess._post("/api/v1/transfers/create", json={}) == {"id": "t1"}


# ----------------------------------------------------------------------
# Clover: the POST/DELETE write verbs
# ----------------------------------------------------------------------


def _clover_session():
    from crude_clover.client import CloverSession

    return CloverSession("token")


def test_clover_writes_refuse(bound, monkeypatch):
    sess = _clover_session()
    monkeypatch.setattr(sess.session, "request", _no_network)
    with pytest.raises(asof.WorldAsOfError):
        sess.post("/v3/merchants/M/items", json={"name": "x"})
    with pytest.raises(asof.WorldAsOfError):
        sess.delete("/v3/merchants/M/items/I1")


def test_clover_get_still_reads_under_bound(bound, monkeypatch):
    sess = _clover_session()
    monkeypatch.setattr(sess.session, "request",
                        lambda method, url, **kw: _FakeResp(body={"elements": []}))
    assert sess.get("/v3/merchants/M/items") == {"elements": []}


def test_clover_write_passes_when_unbound(unbound, monkeypatch):
    sess = _clover_session()
    monkeypatch.setattr(sess.session, "request",
                        lambda method, url, **kw: _FakeResp(body={"id": "I1"}))
    assert sess.post("/v3/merchants/M/items", json={}) == {"id": "I1"}
