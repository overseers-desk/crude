"""The Sonas guest list exports inetOrgPerson LDIF.

`guest list` gains a --ldif flag that writes concatenation-safe LDIF on stdout
instead of a table, mapping firstname/lastname onto givenName/sn and unwrapping
the EJSON epoch-ms createdAt/updatedAt stamps. The --json path is unchanged.
"""

import json
from types import SimpleNamespace

import pytest
import typer

import crude_sonas.cli as cli

CONFIG = {
    "timezone": "Australia/Brisbane",
    "base_dn": "ou=people,dc=example,dc=com",
    "sonas": {"username": "u", "password_hash": "h"},
}

# 1709284500000 ms == 2024-03-01T09:15:00Z == +10:00 09:15 the next hour... 19:15.
GUEST = {
    "_id": "G1",
    "firstname": "Ada",
    "lastname": "Lovelace",
    "createdAt": {"$date": 1709284500000},
    "updatedAt": {"$date": 1709284500000},
}


@pytest.fixture
def stub(monkeypatch):
    def read_pub(name, params, **kwargs):
        if name == "eventBasicInfo":
            return [{"_collection": "events", "_id": "E1"}]
        if name == "guests":
            return [GUEST]
        return []

    client = SimpleNamespace(read_pub=read_pub, close=lambda: None)
    monkeypatch.setattr(cli, "find_config", lambda: "config.toml")
    monkeypatch.setattr(cli, "read_config", lambda path: CONFIG)
    monkeypatch.setattr(cli, "_make_client", lambda config: client)
    return client


def test_guest_list_ldif(stub, capsys):
    cli.guest_list(event_id="E1", output_json=False, ldif=True)
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0] == "dn: uid=sonas-G1,ou=people,dc=example,dc=com"
    assert "objectClass: inetOrgPerson" in lines
    assert "uid: sonas-G1" in lines
    assert "givenName: Ada" in lines
    assert "sn: Lovelace" in lines
    assert "cn: Ada Lovelace" in lines
    assert "createdDateTime: 2024-03-01T19:15:00+10:00" in lines
    assert "modifiedDateTime: 2024-03-01T19:15:00+10:00" in lines
    assert "found." not in out and "{" not in out


def test_guest_list_json_unchanged(stub, capsys):
    cli.guest_list(event_id="E1", output_json=True, ldif=False)
    assert json.loads(capsys.readouterr().out) == [GUEST]


def test_ldif_and_json_conflict(stub):
    with pytest.raises(typer.BadParameter):
        cli.guest_list(event_id="E1", output_json=True, ldif=True)
