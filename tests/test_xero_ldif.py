"""The crude-xero person resources export inetOrgPerson LDIF.

The generic builders in cli_accounting and cli_payroll take an optional
PersonMap. The two people-shaped resources, Accounting `contact` and Payroll AU
`pay-employee`, are built with one, so their `list`/`get` gain a --ldif flag that
writes concatenation-safe LDIF on stdout instead of a table. Contacts map
Name/FirstName/LastName/EmailAddress and the DEFAULT phone onto
cn/givenName/sn/mail/telephoneNumber, and UpdatedDateUTC (in either Xero form)
onto modifiedDateTime; employees map FirstName/LastName onto givenName/sn. A
non-person resource built by the same builder gains no flag, and the --json path
is unchanged.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import typer
from typer.testing import CliRunner

import crude_xero.cli as xcli
from crude_xero import cli_accounting, cli_payroll

runner = CliRunner()

CONFIG = {
    "timezone": "Australia/Brisbane",
    "base_dn": "ou=people,dc=example,dc=com",
    "xero": {"client_id": "c", "client_secret": "s"},
}

# 1709284500000 ms == 2024-03-01T09:15:00Z == 2024-03-01T19:15:00+10:00 (Brisbane).
CONTACT = {
    "ContactID": "C1",
    "Name": "Ada Lovelace",
    "FirstName": "Ada",
    "LastName": "Lovelace",
    "EmailAddress": "ada@example.com",
    "Phones": [
        {"PhoneType": "MOBILE", "PhoneNumber": "999"},
        {"PhoneType": "DEFAULT", "PhoneCountryCode": "64",
         "PhoneAreaCode": "9", "PhoneNumber": "1234567"},
    ],
    "UpdatedDateUTC": "/Date(1709284500000+0000)/",
}

EMPLOYEE = {
    "EmployeeID": "E1",
    "FirstName": "Grace",
    "LastName": "Hopper",
    "UpdatedDateUTC": "/Date(1709284500000+0000)/",
}


def _accounting_client():
    return SimpleNamespace(accounting=SimpleNamespace(
        list_contacts=lambda **k: [CONTACT],
        get_contact=lambda guid: CONTACT,
    ))


def _payroll_client():
    return SimpleNamespace(payroll=SimpleNamespace(
        list_employees=lambda **k: [EMPLOYEE],
        get_employee=lambda guid: EMPLOYEE,
    ))


def _app(register, cli_module, client, monkeypatch):
    """A bare app with just the resources under test, config and client stubbed."""
    monkeypatch.setattr(xcli, "_client", lambda *a, **k: client)
    monkeypatch.setattr(cli_module, "find_config", lambda: "config.toml")
    monkeypatch.setattr(cli_module, "read_config", lambda path: CONFIG)
    app = typer.Typer()
    register(app)
    return app


# ----------------------------------------------------------------------
# Accounting contact
# ----------------------------------------------------------------------


def test_contact_list_ldif(monkeypatch):
    app = _app(cli_accounting.register, cli_accounting, _accounting_client(), monkeypatch)
    result = runner.invoke(app, ["contact", "list", "--ldif"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == "dn: uid=xero-C1,ou=people,dc=example,dc=com"
    assert "objectClass: inetOrgPerson" in lines
    assert "uid: xero-C1" in lines
    assert "cn: Ada Lovelace" in lines
    assert "givenName: Ada" in lines
    assert "sn: Lovelace" in lines
    assert "mail: ada@example.com" in lines
    assert "telephoneNumber: 6491234567" in lines
    assert "modifiedDateTime: 2024-03-01T19:15:00+10:00" in lines
    # createdDateTime is not exposed, so it is unset.
    assert "createdDateTime" not in result.output
    # Pure LDIF only: no table count line, no JSON.
    assert "found." not in result.output and "{" not in result.output


def test_contact_get_ldif(monkeypatch):
    app = _app(cli_accounting.register, cli_accounting, _accounting_client(), monkeypatch)
    result = runner.invoke(app, ["contact", "get", "C1", "--ldif"])
    assert result.exit_code == 0, result.output
    assert result.output.startswith("dn: uid=xero-C1,ou=people,dc=example,dc=com")


def test_contact_iso_updated_date_also_parses(monkeypatch):
    iso = dict(CONTACT, UpdatedDateUTC="2024-03-01T09:15:00Z")
    client = SimpleNamespace(accounting=SimpleNamespace(get_contact=lambda guid: iso))
    app = _app(cli_accounting.register, cli_accounting, client, monkeypatch)
    result = runner.invoke(app, ["contact", "get", "C1", "--ldif"])
    assert result.exit_code == 0, result.output
    assert "modifiedDateTime: 2024-03-01T19:15:00+10:00" in result.output


def test_contact_list_json_unchanged(monkeypatch):
    app = _app(cli_accounting.register, cli_accounting, _accounting_client(), monkeypatch)
    result = runner.invoke(app, ["contact", "list", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == [CONTACT]


def test_non_person_resource_has_no_ldif_flag(monkeypatch):
    # `account` is built by the same _resource factory but without a PersonMap,
    # so it gains no --ldif flag.
    app = _app(cli_accounting.register, cli_accounting, _accounting_client(), monkeypatch)
    result = runner.invoke(app, ["account", "list", "--ldif"])
    assert result.exit_code != 0
    assert "No such option" in result.output


# ----------------------------------------------------------------------
# Payroll AU pay-employee
# ----------------------------------------------------------------------


def test_pay_employee_list_ldif(monkeypatch):
    app = _app(cli_payroll.register, cli_payroll, _payroll_client(), monkeypatch)
    result = runner.invoke(app, ["pay-employee", "list", "--ldif"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == "dn: uid=xero-E1,ou=people,dc=example,dc=com"
    assert "givenName: Grace" in lines
    assert "sn: Hopper" in lines
    assert "cn: Grace Hopper" in lines
    assert "modifiedDateTime: 2024-03-01T19:15:00+10:00" in lines
    assert "found." not in result.output and "{" not in result.output


def test_pay_employee_get_ldif(monkeypatch):
    app = _app(cli_payroll.register, cli_payroll, _payroll_client(), monkeypatch)
    result = runner.invoke(app, ["pay-employee", "get", "E1", "--ldif"])
    assert result.exit_code == 0, result.output
    assert result.output.startswith("dn: uid=xero-E1,ou=people,dc=example,dc=com")


def test_pay_run_has_no_ldif_flag(monkeypatch):
    # `pay-run` shares the builder but carries no PersonMap: no --ldif flag.
    app = _app(cli_payroll.register, cli_payroll, _payroll_client(), monkeypatch)
    result = runner.invoke(app, ["pay-run", "list", "--ldif"])
    assert result.exit_code != 0
    assert "No such option" in result.output


def test_pay_employee_list_json_unchanged(monkeypatch):
    app = _app(cli_payroll.register, cli_payroll, _payroll_client(), monkeypatch)
    result = runner.invoke(app, ["pay-employee", "list", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == [EMPLOYEE]
