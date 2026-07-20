"""crude_common.ldif turns people records into concatenation-safe LDIF.

The stdout stream must carry LDIF and blank separators only, values that RFC
2849 cannot carry verbatim must go base64, and timestamps must land in one
declared timezone. Australia/Brisbane is pinned as in tests/test_localtime.py
so the offset assertions are deterministic on any host.
"""

import base64
from datetime import timezone
from zoneinfo import ZoneInfo

import pytest
import typer

from crude_common.ldif import (
    LdifSink,
    PersonMap,
    emit_ldif,
    parse_epoch_ms,
    parse_naive_utc,
)
from crude_common.output import emit_list, emit_record

BNE = ZoneInfo("Australia/Brisbane")
BASE = "ou=people,dc=example,dc=com"

PM = PersonMap(
    attrs={
        "givenName": "first",
        "sn": "last",
        "mail": "email",
        "telephoneNumber": "phone",
    },
    id_key="id",
    created="created",
    modified="modified",
)


def run(items, pm=PM, capsys=None, site="rezdy"):
    emit_ldif(items, pm, site, BNE, BASE)
    return capsys.readouterr()


def test_dn_uid_and_basic_entry(capsys):
    out = run([{"id": 7, "first": "Ada", "last": "Lovelace",
                "email": "ada@example.com"}], capsys=capsys).out
    lines = out.splitlines()
    assert lines[0] == f"dn: uid=rezdy-7,{BASE}"
    assert "objectClass: inetOrgPerson" in lines
    assert "objectClass: extensibleObject" in lines
    assert "uid: rezdy-7" in lines
    assert "givenName: Ada" in lines
    assert "sn: Lovelace" in lines
    assert "cn: Ada Lovelace" in lines
    assert "mail: ada@example.com" in lines


def test_empty_and_false_attributes_omitted(capsys):
    out = run([{"id": 1, "first": "Bo", "last": "Ek", "email": "",
                "phone": False}], capsys=capsys).out
    assert "mail" not in out
    assert "telephoneNumber" not in out


def test_missing_name_skipped_with_stderr_warning(capsys):
    res = run([{"id": 9, "email": "x@example.com"}], capsys=capsys)
    assert res.out == ""
    assert "skipping" in res.err and "9" in res.err


def test_full_name_used_as_sn_when_no_sn(capsys):
    pm = PersonMap(attrs={"cn": "name"}, id_key="id")
    out = run([{"id": 2, "name": "Cher"}], pm=pm, capsys=capsys).out
    assert "cn: Cher" in out
    assert "sn: Cher" in out


def test_include_filter_skips_with_note(capsys):
    pm = PM._replace(include=lambda r: not r.get("company"))
    res = run([{"id": 3, "first": "A", "last": "B", "company": True}],
              pm=pm, capsys=capsys)
    assert res.out == ""
    assert "skipping" in res.err


def test_non_ascii_value_goes_base64(capsys):
    out = run([{"id": 4, "first": "Zoe", "last": "Müller"}], capsys=capsys).out
    b64 = base64.b64encode("Müller".encode()).decode()
    assert f"sn:: {b64}" in out
    assert "sn: Müller" not in out


def test_leading_space_and_colon_go_base64(capsys):
    out = run([{"id": 5, "first": " lead", "last": ":colon"}],
              capsys=capsys).out
    assert "givenName:: " in out
    assert "sn:: " in out


def test_long_line_folded_at_76_bytes_with_space_continuation(capsys):
    long_mail = "a" * 100 + "@example.com"
    out = run([{"id": 6, "first": "A", "last": "B", "email": long_mail}],
              capsys=capsys).out
    folded = [l for l in out.splitlines() if l.startswith("mail: ")]
    assert folded, out
    line_index = out.splitlines().index(folded[0])
    continuation = out.splitlines()[line_index + 1]
    assert len(folded[0].encode()) == 76
    assert continuation.startswith(" ") and not continuation.startswith("  ")
    # Unfolding restores the value.
    assert (folded[0] + continuation[1:]) == f"mail: {long_mail}"


def test_timestamps_rendered_in_tz_with_offset(capsys):
    out = run([{"id": 8, "first": "A", "last": "B",
                "created": "2024-02-29T23:15:00Z",
                "modified": "2024-03-01T00:00:00Z"}], capsys=capsys).out
    assert "createdDateTime: 2024-03-01T09:15:00+10:00" in out
    assert "modifiedDateTime: 2024-03-01T10:00:00+10:00" in out


def test_custom_parse_dt(capsys):
    pm = PM._replace(parse_dt=parse_naive_utc)
    out = run([{"id": 8, "first": "A", "last": "B",
                "created": "2024-02-29 23:15:00"}], capsys=capsys).out
    assert "createdDateTime: 2024-03-01T09:15:00+10:00" in out


def test_concatenation_purity(capsys):
    items = [{"id": i, "first": "A", "last": f"B{i}"} for i in (1, 2)]
    out = run(items, capsys=capsys).out
    entries = out.split("\n\n")
    assert len(entries) == 2
    assert not out.endswith("\n\n")
    for entry in entries:
        assert entry.strip().startswith("dn: uid=rezdy-")


def test_parse_epoch_ms():
    dt = parse_epoch_ms(1709284500000)
    assert dt == parse_epoch_ms("1709284500000")
    assert dt.tzinfo is timezone.utc
    assert dt.isoformat() == "2024-03-01T09:15:00+00:00"
    assert parse_epoch_ms(None) is None
    assert parse_epoch_ms("nope") is None


def test_parse_naive_utc():
    dt = parse_naive_utc("2024-03-01 09:15:00")
    assert dt.tzinfo is timezone.utc and dt.hour == 9
    assert parse_naive_utc("2024-03-01") is None
    assert parse_naive_utc(False) is None


SINK = LdifSink(pm=PM, site="rezdy", tz=BNE, base_dn=BASE)


def test_emit_list_routes_to_ldif(capsys):
    emit_list([{"id": 1, "first": "A", "last": "B"}], [("Id", "id")],
              "person", output_json=False, ldif=SINK)
    out = capsys.readouterr().out
    assert out.startswith("dn: uid=rezdy-1,")
    assert "found." not in out and "{" not in out


def test_emit_record_routes_to_ldif(capsys):
    emit_record({"id": 2, "first": "A", "last": "B"}, output_json=False,
                ldif=SINK)
    out = capsys.readouterr().out
    assert out.startswith("dn: uid=rezdy-2,")


def test_json_plus_ldif_conflict():
    with pytest.raises(typer.BadParameter):
        emit_list([], [], "person", output_json=True, ldif=SINK)


def test_emit_list_json_path_unchanged(capsys):
    emit_list([{"id": 1}], [("Id", "id")], "person", output_json=True)
    assert capsys.readouterr().out == '[\n  {\n    "id": 1\n  }\n]\n'


def test_emit_list_table_path_unchanged(capsys):
    emit_list([{"id": 1}], [("Id", "id")], "person", output_json=False)
    out = capsys.readouterr().out
    assert "1 person(s) found." in out


def test_resolve_timezone_precedence_and_failure():
    from crude_common.config import resolve_base_dn, resolve_timezone

    assert resolve_timezone({"timezone": "UTC"},
                            {"timezone": "Australia/Brisbane"}) == BNE
    assert resolve_timezone({"timezone": "UTC"}, {}) == ZoneInfo("UTC")
    assert resolve_timezone({}, {}) is not None  # machine zone fallback
    with pytest.raises(typer.Exit):
        resolve_timezone({"timezone": "Mars/Olympus"}, {})
    assert resolve_base_dn({}) == "ou=people,dc=crude,dc=local"
    assert resolve_base_dn({"base_dn": BASE}) == BASE
