"""Unit tests for the rezdy customer read verbs and their LDIF export.

The transport is monkeypatched, so nothing reaches the network. These pin the
new --ldif path (a parseable inetOrgPerson entry with the expected dn and
attributes) and confirm the --json path is unchanged.
"""

import json

import pytest

from crude_rezdy import cli

BASE = "ou=people,dc=example,dc=com"


class _FakeClient:
    def __init__(self, customers):
        self._customers = customers

    def list_customers(self, search=None, limit=20, offset=0):
        return self._customers

    def get_customer(self, customer_id):
        return self._customers[0]


@pytest.fixture
def wired(monkeypatch):
    config = {
        "base_dn": BASE,
        "rezdy": {"api_key": "KEY", "timezone": "Australia/Brisbane"},
    }
    customers = [
        {"id": 7, "firstName": "Ada", "lastName": "Lovelace",
         "email": "ada@example.com", "phone": "+61 7 1234 5678"},
    ]
    monkeypatch.setattr(cli, "find_config", lambda: "config.toml")
    monkeypatch.setattr(cli, "read_config", lambda _p: config)
    monkeypatch.setattr(cli, "_make_client", lambda _c: _FakeClient(customers))
    return customers


def test_list_ldif_emits_parseable_entry(wired, capsys):
    cli.list_customers(search=None, limit=20, offset=0, output_json=False, ldif=True)
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0] == f"dn: uid=rezdy-7,{BASE}"
    assert "objectClass: inetOrgPerson" in lines
    assert "uid: rezdy-7" in lines
    assert "givenName: Ada" in lines
    assert "sn: Lovelace" in lines
    assert "cn: Ada Lovelace" in lines
    assert "mail: ada@example.com" in lines
    assert "telephoneNumber: +61 7 1234 5678" in lines
    # Pure LDIF only: no table count line, no JSON.
    assert "found." not in out and "{" not in out
    # Customers carry no timestamps, so no dateTime attributes.
    assert "createdDateTime" not in out and "modifiedDateTime" not in out


def test_get_ldif_emits_parseable_entry(wired, capsys):
    cli.get_customer(customer_id="7", output_json=False, ldif=True)
    out = capsys.readouterr().out
    assert out.startswith(f"dn: uid=rezdy-7,{BASE}")
    assert "mail: ada@example.com" in out


def test_list_json_unchanged(wired, capsys):
    cli.list_customers(search=None, limit=20, offset=0, output_json=True, ldif=False)
    out = capsys.readouterr().out
    assert json.loads(out) == wired


def test_get_json_unchanged(wired, capsys):
    cli.get_customer(customer_id="7", output_json=True, ldif=False)
    out = capsys.readouterr().out
    assert json.loads(out) == wired[0]
