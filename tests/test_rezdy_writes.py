"""Unit tests for the rezdy write path — no network.

These pin the behaviours the write verbs hinge on: the position-based payload
extraction, Rezdy's in-body error channel, the read-merge-write that lets a
single field change without dropping the rest, the booking-create notification default
default, and the cached id->name resolver. The transport is monkeypatched, so
nothing reaches the network.
"""

import pytest
import typer

from crude_rezdy import cli
from crude_rezdy.client import RezdyClient, _payload


class _FakeResp:
    def __init__(self, payload, ok=True, status_code=200):
        self._payload = payload
        self.ok = ok
        self.status_code = status_code

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_payload_extracts_the_single_non_status_key():
    assert _payload({"requestStatus": {}, "products": [1, 2]}) == [1, 2]
    assert _payload({"requestStatus": {}, "product": {"a": 1}}) == {"a": 1}


def test_payload_returns_whole_dict_when_shape_is_unusual():
    # A bare requestStatus (e.g. a 200 DELETE ack) has no resource key to pull.
    only_status = {"requestStatus": {"success": True}}
    assert _payload(only_status) == only_status


def test_request_sends_method_body_and_apikey(monkeypatch):
    client = RezdyClient("KEY")
    captured = {}

    def fake_request(method, url, params=None, json=None):
        captured.update(method=method, url=url, params=params, json=json)
        return _FakeResp({"requestStatus": {"success": True}, "product": {"productCode": "P1"}})

    monkeypatch.setattr(client.session, "request", fake_request)
    out = client.create_product({"name": "X"})

    assert captured["method"] == "POST"
    assert captured["url"].endswith("/v1/products")
    # The key travels in the apiKey header and never in the query string, where
    # requests would quote it back in the text of a connection or HTTP error.
    assert client.session.headers["apiKey"] == "KEY"
    assert "apiKey" not in captured["params"]
    assert captured["json"] == {"name": "X"}
    assert out == {"productCode": "P1"}


def test_request_surfaces_in_body_error(monkeypatch):
    client = RezdyClient("KEY")

    def fake_request(method, url, params=None, json=None):
        return _FakeResp({"requestStatus": {"success": False, "error": {"errorMessage": "bad code"}}})

    monkeypatch.setattr(client.session, "request", fake_request)
    with pytest.raises(RuntimeError) as exc:
        client.get_product("P1")
    assert "bad code" in str(exc.value)


def test_empty_body_on_delete_is_tolerated(monkeypatch):
    client = RezdyClient("KEY")
    monkeypatch.setattr(client.session, "request",
                        lambda *a, **k: _FakeResp(None, ok=True, status_code=200))
    assert client.delete_product("P1") == {}


def test_merge_update_preserves_other_fields():
    current = {"productCode": "P1", "name": "Old", "terms": "old terms", "advertisedPrice": 10}
    captured = {}

    def update_fn(merged):
        captured["merged"] = merged
        return {"productCode": "P1"}

    cli.merge_update(lambda: current, update_fn, None, None,
                      {"terms": "NEW"}, "update product P1", yes=True, output_json=True)

    assert captured["merged"]["terms"] == "NEW"
    assert captured["merged"]["name"] == "Old"
    assert captured["merged"]["advertisedPrice"] == 10
    assert captured["merged"]["productCode"] == "P1"


def test_merge_update_clears_with_empty_string():
    captured = {}
    cli.merge_update(lambda: {"productCode": "P1", "terms": "x"},
                      lambda m: captured.update(merged=m), None, None,
                      {"terms": ""}, "update product P1", yes=True, output_json=True)
    assert captured["merged"]["terms"] == ""


def test_merge_update_requires_a_change():
    with pytest.raises(typer.Exit):
        cli.merge_update(lambda: {"productCode": "P1"}, lambda m: None, None, None,
                          {"name": None}, "update", yes=True, output_json=True)


def _fake_booking_client(monkeypatch):
    captured = {"n": 0}

    class FakeClient:
        def create_booking(self, body):
            captured["n"] += 1
            captured["body"] = body
            return {"orderNumber": "R1"}

    monkeypatch.setattr(cli, "_client", lambda: FakeClient())
    return captured


def _create(**kw):
    args = dict(data='{"items": []}', file=None, no_notify=False, yes=True, output_json=True)
    args.update(kw)
    cli.create_booking(**args)


def test_booking_create_notifies_by_default(monkeypatch):
    captured = _fake_booking_client(monkeypatch)
    _create()
    assert captured["body"]["sendNotifications"] is True
    _create(data='{"items": [], "sendNotifications": true}')
    assert captured["body"]["sendNotifications"] is True


@pytest.mark.parametrize("kw", [
    {"no_notify": True},
    {"data": '{"items": [], "sendNotifications": false}'},
    {"no_notify": True, "data": '{"items": [], "sendNotifications": true}'},
])
def test_booking_create_suppression_declined_creates_nothing(monkeypatch, kw):
    captured = _fake_booking_client(monkeypatch)
    seen = {}

    def decline(text, default=None, abort=False, **_):
        seen.update(default=default, text=text)
        raise typer.Abort()

    monkeypatch.setattr(cli.typer, "confirm", decline)
    with pytest.raises(typer.Abort):
        _create(**kw)  # yes=True does not skip the suppression prompt
    assert seen["default"] is False
    assert captured["n"] == 0


def test_booking_create_suppression_confirmed_sends_false(monkeypatch):
    captured = _fake_booking_client(monkeypatch)
    monkeypatch.setattr(cli.typer, "confirm", lambda *a, **k: True)
    _create(no_notify=True)
    assert captured["body"]["sendNotifications"] is False


def test_product_names_resolver_is_cached(monkeypatch):
    client = RezdyClient("KEY")
    calls = {"n": 0}

    def fake_list_products(search=None, limit=20, offset=0):
        calls["n"] += 1
        return [{"productCode": "P1", "name": "Tour One"}]

    monkeypatch.setattr(client, "list_products", fake_list_products)
    assert client.product_names() == {"P1": "Tour One"}
    assert client.product_names() == {"P1": "Tour One"}
    assert calls["n"] == 1
