"""crude-airwallex Payouts API units: request paths, headers, body assembly — no network.

The session's requests.Session.request is monkeypatched, so nothing reaches the
network. Covers the logic the transport tests (tests/test_airwallex.py) do not: the
write paths the Payouts groups POST to, the idempotency request_id fill on transfer/
conversion create, and the x-api-version header the date-versioned FX endpoints need.
"""

from __future__ import annotations

import time

from crude_airwallex import auth
from crude_airwallex.beneficiaries import BeneficiariesAPI
from crude_airwallex.client import AirwallexSession
from crude_airwallex.fx import FX_API_VERSION, FxAPI
from crude_airwallex.transfers import TransfersAPI


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
    """Monkeypatch the session transport to record one call's method/url/json/headers."""
    seen = {}

    def fake(method, url, **kw):
        seen.update(method=method, url=url, json=kw.get("json"),
                    params=kw.get("params"), headers=kw.get("headers"))
        return _FakeResp(body=body)

    xs.session.request = fake
    return seen


def test_create_transfer_fills_request_id_and_posts_to_create():
    xs = _session()
    seen = _capture(xs)
    TransfersAPI(xs).create_transfer({"transfer_amount": 10, "transfer_currency": "AUD"})
    assert seen["method"] == "POST"
    assert seen["url"].endswith("/api/v1/transfers/create")
    assert seen["json"]["transfer_amount"] == 10
    assert seen["json"]["request_id"]  # a uuid was filled in


def test_create_transfer_keeps_caller_request_id():
    xs = _session()
    seen = _capture(xs)
    TransfersAPI(xs).create_transfer({"request_id": "mine", "transfer_amount": 1})
    assert seen["json"]["request_id"] == "mine"


def test_create_conversion_fills_request_id_and_sends_fx_version():
    xs = _session()
    seen = _capture(xs)
    FxAPI(xs).create_conversion({"buy_currency": "USD", "sell_currency": "AUD"})
    assert seen["url"].endswith("/api/v1/fx/conversions/create")
    assert seen["json"]["request_id"]
    assert seen["headers"]["x-api-version"] == FX_API_VERSION


def test_current_rate_sends_version_and_drops_none_params():
    xs = _session()
    seen = _capture(xs, body={"rate": 0.7})
    FxAPI(xs).get_current_rate(buy_currency="USD", sell_currency="AUD")
    assert seen["url"].endswith("/api/v1/fx/rates/current")
    assert seen["headers"]["x-api-version"] == FX_API_VERSION
    assert seen["params"] == {"buy_currency": "USD", "sell_currency": "AUD"}  # None amounts dropped


def test_list_conversions_sends_version_header():
    xs = _session()
    seen = _capture(xs, body={"items": [], "has_more": False})
    FxAPI(xs).list_conversions()
    assert seen["url"].endswith("/api/v1/fx/conversions")
    assert seen["headers"]["x-api-version"] == FX_API_VERSION


def test_beneficiary_update_and_delete_post_to_id_suffix_paths():
    xs = _session()
    seen = _capture(xs)
    BeneficiariesAPI(xs).update_beneficiary("ben_1", {"nickname": "x"})
    assert seen["method"] == "POST"
    assert seen["url"].endswith("/api/v1/beneficiaries/update/ben_1")

    seen = _capture(xs)
    BeneficiariesAPI(xs).delete_beneficiary("ben_1")
    assert seen["method"] == "POST"
    assert seen["url"].endswith("/api/v1/beneficiaries/delete/ben_1")
