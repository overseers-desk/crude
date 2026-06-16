"""Unit tests for the rezdy write path — no network.

These pin the behaviours the write verbs hinge on: the position-based payload
extraction, Rezdy's in-body error channel, the read-merge-write that lets a
single field change without dropping the rest, the booking-create notification
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
    assert captured["params"]["apiKey"] == "KEY"
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

    cli._merge_update(lambda: current, update_fn, None, None,
                      {"terms": "NEW"}, "update product P1", yes=True, output_json=True)

    assert captured["merged"]["terms"] == "NEW"
    assert captured["merged"]["name"] == "Old"
    assert captured["merged"]["advertisedPrice"] == 10
    assert captured["merged"]["productCode"] == "P1"


def test_merge_update_clears_with_empty_string():
    captured = {}
    cli._merge_update(lambda: {"productCode": "P1", "terms": "x"},
                      lambda m: captured.update(merged=m), None, None,
                      {"terms": ""}, "update product P1", yes=True, output_json=True)
    assert captured["merged"]["terms"] == ""


def test_merge_update_requires_a_change():
    with pytest.raises(typer.Exit):
        cli._merge_update(lambda: {"productCode": "P1"}, lambda m: None, None, None,
                          {"name": None}, "update", yes=True, output_json=True)


def test_booking_create_defaults_notifications_off(monkeypatch):
    captured = {}

    class FakeClient:
        def create_booking(self, body):
            captured["body"] = body
            return {"orderNumber": "R1"}

    monkeypatch.setattr(cli, "_client", lambda: FakeClient())

    cli.create_booking(data='{"items": []}', file=None, notify=False, yes=True, output_json=True)
    assert captured["body"]["sendNotifications"] is False

    # The flag is authoritative over a sendNotifications carried in --data.
    cli.create_booking(data='{"items": [], "sendNotifications": true}', file=None,
                       notify=False, yes=True, output_json=True)
    assert captured["body"]["sendNotifications"] is False

    cli.create_booking(data='{"items": []}', file=None, notify=True, yes=True, output_json=True)
    assert captured["body"]["sendNotifications"] is True


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
