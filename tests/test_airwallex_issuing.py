"""crude-airwallex Issuing API units: request paths and body assembly.

The session's requests.Session.request is monkeypatched, so nothing reaches the
network. Covers the issuing write paths (``/create`` for new objects, and the
resource-then-action ``/{id}/update`` shape that differs from the Payouts
``/update/{id}`` convention), the idempotency request_id fill on card/cardholder
create, the partial-body pass-through on update, and the list filter assembly
(the issuing ``from_created_date`` / ``to_created_date`` date params, Nones dropped).
"""

from __future__ import annotations

import time

from crude_airwallex import auth
from crude_airwallex.client import AirwallexSession
from crude_airwallex.issuing import IssuingAPI


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
# cards: create fills request_id; update uses the /{id}/update action path
# ----------------------------------------------------------------------

def test_create_card_fills_request_id_and_posts_to_create():
    xs = _session()
    seen = _capture(xs)
    IssuingAPI(xs).create_card({"cardholder_id": "ch_1", "currency": "AUD"})
    assert seen["method"] == "POST"
    assert seen["url"].endswith("/api/v1/issuing/cards/create")
    assert seen["json"]["cardholder_id"] == "ch_1"
    assert seen["json"]["request_id"]  # a uuid was filled in


def test_create_card_keeps_caller_request_id():
    xs = _session()
    seen = _capture(xs)
    IssuingAPI(xs).create_card({"request_id": "mine", "cardholder_id": "ch_1"})
    assert seen["json"]["request_id"] == "mine"


def test_update_card_posts_to_id_action_path_with_partial_body():
    xs = _session()
    seen = _capture(xs)
    IssuingAPI(xs).update_card("card_1", {"card_status": "INACTIVE"})
    assert seen["method"] == "POST"
    assert seen["url"].endswith("/api/v1/issuing/cards/card_1/update")
    # Partial body passed straight through: no fetched record, no forced request_id.
    assert seen["json"] == {"card_status": "INACTIVE"}


def test_list_cards_maps_issuing_date_params_and_drops_none():
    xs = _session()
    seen = _capture(xs, body={"items": [], "has_more": False})
    IssuingAPI(xs).list_cards(cardholder_id="ch_1", from_="2026-01-01T00:00:00Z")
    assert seen["url"].endswith("/api/v1/issuing/cards")
    assert seen["params"]["cardholder_id"] == "ch_1"
    # Issuing uses the _date form, not the financial-transactions _at form.
    assert seen["params"]["from_created_date"] == "2026-01-01T00:00:00Z"
    assert "to_created_date" not in seen["params"]  # None dropped
    assert "status" not in seen["params"]  # None dropped


# ----------------------------------------------------------------------
# cardholders: create fills request_id; update uses the /{id}/update path
# ----------------------------------------------------------------------

def test_create_cardholder_fills_request_id_and_posts_to_create():
    xs = _session()
    seen = _capture(xs)
    IssuingAPI(xs).create_cardholder({"type": "INDIVIDUAL", "email": "a@example.com"})
    assert seen["url"].endswith("/api/v1/issuing/cardholders/create")
    assert seen["json"]["type"] == "INDIVIDUAL"
    assert seen["json"]["request_id"]


def test_update_cardholder_posts_to_id_action_path():
    xs = _session()
    seen = _capture(xs)
    IssuingAPI(xs).update_cardholder("ch_1", {"email": "b@example.com"})
    assert seen["method"] == "POST"
    assert seen["url"].endswith("/api/v1/issuing/cardholders/ch_1/update")
    assert seen["json"] == {"email": "b@example.com"}


# ----------------------------------------------------------------------
# authorizations + transactions: read-only paths and filter assembly
# ----------------------------------------------------------------------

def test_get_authorization_and_transaction_paths():
    xs = _session()
    seen = _capture(xs, body={"transaction_id": "txn_1"})
    IssuingAPI(xs).get_authorization("auth_1")
    assert seen["method"] == "GET"
    assert seen["url"].endswith("/api/v1/issuing/authorizations/auth_1")

    seen = _capture(xs, body={"transaction_id": "txn_1"})
    IssuingAPI(xs).get_transaction("txn_1")
    assert seen["method"] == "GET"
    assert seen["url"].endswith("/api/v1/issuing/transactions/txn_1")


def test_list_transactions_maps_card_and_date_filters():
    xs = _session()
    seen = _capture(xs, body={"items": [], "has_more": False})
    IssuingAPI(xs).list_transactions(card_id="card_1", status="CLEARED",
                                     to="2026-06-01T00:00:00Z")
    assert seen["url"].endswith("/api/v1/issuing/transactions")
    assert seen["params"]["card_id"] == "card_1"
    assert seen["params"]["status"] == "CLEARED"
    assert seen["params"]["to_created_date"] == "2026-06-01T00:00:00Z"
    assert "from_created_date" not in seen["params"]  # None dropped
