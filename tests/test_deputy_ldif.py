"""The Deputy employee people commands export inetOrgPerson LDIF.

`employee list`/`employee get` gain a --ldif flag that writes concatenation-safe
LDIF on stdout instead of a table, mapping DisplayName/FirstName/LastName onto
cn/givenName/sn and the Created/Modified audit stamps onto the dateTime attrs.
The --json path is unchanged.
"""

import json
from types import SimpleNamespace

import pytest
import typer

import crude_deputy.cli as cli

CONFIG = {
    "timezone": "Australia/Brisbane",
    "base_dn": "ou=people,dc=example,dc=com",
    "deputy": {"deputy_api_token": "t", "deputy_install": "i", "deputy_geo": "g"},
}

EMPLOYEE = {
    "Id": 42,
    "DisplayName": "Ada Lovelace",
    "FirstName": "Ada",
    "LastName": "Lovelace",
    "Active": True,
    "Created": "2024-03-01T09:15:00+10:00",
    "Modified": "2024-03-02T09:15:00+10:00",
}


@pytest.fixture
def stub(monkeypatch):
    client = SimpleNamespace(
        query_resource=lambda *a, **k: [EMPLOYEE],
        paginate_query=lambda *a, **k: [EMPLOYEE],
        get_resource=lambda obj, id: EMPLOYEE,
    )
    monkeypatch.setattr(cli, "find_config", lambda: "config.toml")
    monkeypatch.setattr(cli, "read_config", lambda path: CONFIG)
    monkeypatch.setattr(cli, "_make_client", lambda config: client)
    return client


def test_employee_list_ldif(stub, capsys):
    cli.employee_list(limit=50, fetch_all=False, output_json=False, ldif=True)
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0] == "dn: uid=deputy-42,ou=people,dc=example,dc=com"
    assert "objectClass: inetOrgPerson" in lines
    assert "uid: deputy-42" in lines
    assert "cn: Ada Lovelace" in lines
    assert "givenName: Ada" in lines
    assert "sn: Lovelace" in lines
    assert "createdDateTime: 2024-03-01T09:15:00+10:00" in lines
    assert "modifiedDateTime: 2024-03-02T09:15:00+10:00" in lines
    # Pure LDIF only: no table count line, no JSON.
    assert "found." not in out and "{" not in out


def test_employee_get_ldif(stub, capsys):
    cli.employee_get(id="42", output_json=False, ldif=True)
    out = capsys.readouterr().out
    assert out.startswith("dn: uid=deputy-42,ou=people,dc=example,dc=com")


def test_employee_list_json_unchanged(stub, capsys):
    cli.employee_list(limit=50, fetch_all=False, output_json=True, ldif=False)
    assert json.loads(capsys.readouterr().out) == [EMPLOYEE]


def test_ldif_and_json_conflict(stub):
    with pytest.raises(typer.BadParameter):
        cli.employee_list(limit=50, fetch_all=False, output_json=True, ldif=True)
