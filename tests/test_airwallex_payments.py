"""crude-airwallex Payments Acceptance API units: request paths and body assembly.

The session's requests.Session.request is monkeypatched, so nothing reaches the
network. Covers the `pa` group's write paths (the POST-with-suffix `/create`,
`/update/{id}`, `/delete/{id}` and the payment intent's `/{id}/confirm|capture|
cancel` action paths), the idempotency request_id fill on the money verbs, and the
list filter assembly.
"""

from __future__ import annotations

import time

from crude_airwallex import auth
from crude_airwallex.client import AirwallexSession
from crude_airwallex.payments import PaymentsAPI


class _FakeResp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._body = {} if body is None else body
        self.headers = {}
        self.content = b"x"

    def json(self):
        return self._body


def _session():
    return AirwallexSession(
        "acct", "cid", "key", base=auth.PROD_BASE,
        token={"token": "TOK", "expires_at": time.time() + 9999},
    )


def _capture(xs, body=None):
    """Monkeypatch the session transport to record one call's method/url/json/params."""
    seen = {}

    def fake(method, url, **kw):
        seen.update(method=method, url=url, json=kw.get("json"),
                    params=kw.get("params"), headers=kw.get("headers"))
        return _FakeResp(body=body)

    xs.session.request = fake
    return seen


# ----------------------------------------------------------------------
# payment intents: create + the action paths, all filling request_id
# ----------------------------------------------------------------------

def test_create_payment_intent_fills_request_id_and_posts_to_create():
    xs = _session()
    seen = _capture(xs)
    PaymentsAPI(xs).create_payment_intent({"amount": 10, "currency": "USD"})
    assert seen["method"] == "POST"
    assert seen["url"].endswith("/api/v1/pa/payment_intents/create")
    assert seen["json"]["amount"] == 10
    assert seen["json"]["request_id"]  # a uuid was filled in


def test_payment_intent_action_paths_and_request_id():
    xs = _session()
    for verb, suffix in (("confirm", "confirm"), ("capture", "capture"), ("cancel", "cancel")):
        seen = _capture(xs)
        getattr(PaymentsAPI(xs), f"{verb}_payment_intent")("int_1", {})
        assert seen["method"] == "POST"
        assert seen["url"].endswith(f"/api/v1/pa/payment_intents/int_1/{suffix}")
        assert seen["json"]["request_id"]  # filled even on an empty body


def test_payment_intent_keeps_caller_request_id():
    xs = _session()
    seen = _capture(xs)
    PaymentsAPI(xs).capture_payment_intent("int_1", {"request_id": "mine", "amount": 5})
    assert seen["json"]["request_id"] == "mine"


def test_list_payment_intents_drops_none_and_maps_date_params():
    xs = _session()
    seen = _capture(xs, body={"items": [], "has_more": False})
    PaymentsAPI(xs).list_payment_intents(status="SUCCEEDED", from_="2026-01-01T00:00:00Z")
    assert seen["url"].endswith("/api/v1/pa/payment_intents")
    assert seen["params"]["status"] == "SUCCEEDED"
    assert seen["params"]["from_created_at"] == "2026-01-01T00:00:00Z"
    assert "to_created_at" not in seen["params"]  # None dropped


# ----------------------------------------------------------------------
# refunds: create fills request_id
# ----------------------------------------------------------------------

def test_create_refund_fills_request_id_and_posts_to_create():
    xs = _session()
    seen = _capture(xs)
    PaymentsAPI(xs).create_refund({"payment_intent_id": "int_1", "amount": 5})
    assert seen["url"].endswith("/api/v1/pa/refunds/create")
    assert seen["json"]["payment_intent_id"] == "int_1"
    assert seen["json"]["request_id"]


# ----------------------------------------------------------------------
# customers: no money, no request_id; POST-with-suffix update/delete paths
# ----------------------------------------------------------------------

def test_create_customer_passes_body_without_request_id():
    xs = _session()
    seen = _capture(xs)
    PaymentsAPI(xs).create_customer({"merchant_customer_id": "c1"})
    assert seen["url"].endswith("/api/v1/pa/customers/create")
    assert seen["json"] == {"merchant_customer_id": "c1"}  # no idempotency key forced


def test_customer_update_and_delete_post_to_id_suffix_paths():
    xs = _session()
    seen = _capture(xs)
    PaymentsAPI(xs).update_customer("cus_1", {"email": "a@example.com"})
    assert seen["method"] == "POST"
    assert seen["url"].endswith("/api/v1/pa/customers/update/cus_1")

    seen = _capture(xs)
    PaymentsAPI(xs).delete_customer("cus_1")
    assert seen["method"] == "POST"
    assert seen["url"].endswith("/api/v1/pa/customers/delete/cus_1")


# ----------------------------------------------------------------------
# payment consents (read-only) + payment links (create fills request_id)
# ----------------------------------------------------------------------

def test_get_payment_consent_path():
    xs = _session()
    seen = _capture(xs, body={"id": "cst_1"})
    PaymentsAPI(xs).get_payment_consent("cst_1")
    assert seen["method"] == "GET"
    assert seen["url"].endswith("/api/v1/pa/payment_consents/cst_1")


def test_create_payment_link_fills_request_id_and_posts_to_create():
    xs = _session()
    seen = _capture(xs)
    PaymentsAPI(xs).create_payment_link({"title": "Booking", "amount": 100, "currency": "AUD"})
    assert seen["url"].endswith("/api/v1/pa/payment_links/create")
    assert seen["json"]["title"] == "Booking"
    assert seen["json"]["request_id"]
