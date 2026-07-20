"""Unit tests for the skal member read verbs and their LDIF export.

The client is monkeypatched, so nothing reaches the network. These pin the new
--ldif path (a parseable inetOrgPerson entry with the expected dn and
attributes, and Odoo's naive-UTC create/write dates rendered in the configured
zone) and confirm the --json path is unchanged.
"""

import json

import pytest

from crude_skal import cli

BASE = "ou=people,dc=example,dc=com"


class _FakeClient:
    def __init__(self, members):
        self._members = members

    def list_members(self, limit=100, offset=0):
        return self._members

    def search_members(self, domain, limit=20, offset=0):
        return self._members

    def get_member(self, member_id):
        return self._members[0]


LIST_MEMBER = {
    "id": 42, "name": "Bruce Wayne", "work_email": "bruce@example.com",
    "principal_work_company": "Wayne Enterprises",
    "principal_work_position": "CEO",
    "create_date": "2024-02-29 23:15:00", "write_date": "2024-03-01 00:00:00",
}

DETAIL_MEMBER = dict(
    LIST_MEMBER,
    first_name="Bruce", last_name="Wayne",
    work_phone="+61 3 1000 2000", work_mobile="+61 400 000 000",
)


@pytest.fixture
def wired(monkeypatch):
    config = {
        "base_dn": BASE,
        "skal": {"session_id": "sid", "timezone": "Australia/Brisbane"},
    }
    monkeypatch.setattr(cli, "find_config", lambda: "config.toml")
    monkeypatch.setattr(cli, "read_config", lambda _p: config)
    monkeypatch.setattr(cli, "_make_client", lambda _c: _FakeClient([LIST_MEMBER]))
    return config


def test_list_ldif_emits_parseable_entry(wired, capsys):
    cli.list_(name=None, city=None, club=None, email=None, member_state=None,
              limit=20, offset=0, output_json=False, ldif=True)
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0] == f"dn: uid=skal-42,{BASE}"
    assert "objectClass: inetOrgPerson" in lines
    assert "uid: skal-42" in lines
    # The list carries a display name only, so cn/sn both come from `name`.
    assert "cn: Bruce Wayne" in lines
    assert "sn: Bruce Wayne" in lines
    assert "mail: bruce@example.com" in lines
    assert "o: Wayne Enterprises" in lines
    assert "title: CEO" in lines
    # Odoo naive UTC rendered in Australia/Brisbane (+10:00).
    assert "createdDateTime: 2024-03-01T09:15:00+10:00" in lines
    assert "modifiedDateTime: 2024-03-01T10:00:00+10:00" in lines
    assert "found." not in out and "{" not in out


def test_get_ldif_uses_detail_map(monkeypatch, wired, capsys):
    monkeypatch.setattr(cli, "_make_client", lambda _c: _FakeClient([DETAIL_MEMBER]))
    cli.get(member_id=42, output_json=False, ldif=True)
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0] == f"dn: uid=skal-42,{BASE}"
    assert "givenName: Bruce" in lines
    assert "sn: Wayne" in lines
    assert "cn: Bruce Wayne" in lines
    assert "telephoneNumber: +61 3 1000 2000" in lines
    assert "mobile: +61 400 000 000" in lines
    assert "o: Wayne Enterprises" in lines
    assert "title: CEO" in lines


def test_list_json_unchanged(wired, capsys):
    cli.list_(name=None, city=None, club=None, email=None, member_state=None,
              limit=20, offset=0, output_json=True, ldif=False)
    out = capsys.readouterr().out
    assert json.loads(out) == [LIST_MEMBER]


def test_get_json_unchanged(monkeypatch, wired, capsys):
    monkeypatch.setattr(cli, "_make_client", lambda _c: _FakeClient([DETAIL_MEMBER]))
    cli.get(member_id=42, output_json=True, ldif=False)
    out = capsys.readouterr().out
    assert json.loads(out) == DETAIL_MEMBER
