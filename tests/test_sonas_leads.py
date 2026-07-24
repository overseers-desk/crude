"""`event leads` counts enquiries by lead source and enquiry date.

The command resolves a --source name to its enquiry_source category id, builds a
selector on ``enquiryData.sourceId`` (and, when given, the ``enquiryData.date``
range), and reports the EventList tabular ``recordsFiltered`` count. --list adds
the matched leads; --json emits the whole result.
"""

import json
from types import SimpleNamespace

import pytest
import typer

import crude_sonas.cli as cli
from crude_sonas.client import to_ejson_date, to_ejson_date_end

CONFIG = {"timezone": "Australia/Brisbane",
          "sonas": {"username": "u", "password_hash": "h"}}

SOURCES = [
    {"_id": "SRC_EASY", "name": "Easy Weddings", "tag": "enquiry_source"},
    {"_id": "SRC_HITCHED", "name": "Hitched", "tag": "enquiry_source"},
    {"_id": "CAT_OTHER", "name": "Easy Weddings", "tag": "heard_about_us"},
]


def _source_client():
    """A stub exposing read_categories, the resolver's category read."""
    return SimpleNamespace(read_categories=lambda: list(SOURCES))


def test_resolve_source_by_name_case_insensitive():
    assert cli._resolve_enquiry_source_id(_source_client(), "easy weddings") == "SRC_EASY"


def test_resolve_source_by_id_passthrough():
    assert cli._resolve_enquiry_source_id(_source_client(), "SRC_HITCHED") == "SRC_HITCHED"


def test_resolve_source_unknown_lists_available():
    client = _source_client()
    with pytest.raises(typer.Exit) as exc:
        cli._resolve_enquiry_source_id(client, "Nowhere")
    assert exc.value.exit_code == 1


def test_resolve_source_ignores_other_tags():
    # The heard_about_us "Easy Weddings" is not an enquiry source, so only the
    # enquiry_source one matches — no ambiguity.
    assert cli._resolve_enquiry_source_id(_source_client(), "Easy Weddings") == "SRC_EASY"


@pytest.fixture
def stub(monkeypatch):
    captured = {}

    def event_ids_matching(selector):
        captured["selector"] = selector
        return ["E1", "E2", "E3"], 3

    def read_pub(name, params, **kwargs):
        if name == "eventBasicInfo":
            return [{"_collection": "events", "_id": params[0], "status": 0,
                     "enquiryData": {"date": {"$date": 1767225600000}}}]
        return []

    client = SimpleNamespace(read_categories=lambda: list(SOURCES),
                             event_ids_matching=event_ids_matching,
                             read_pub=read_pub, close=lambda: None)
    monkeypatch.setattr(cli, "find_config", lambda: "config.toml")
    monkeypatch.setattr(cli, "read_config", lambda path: CONFIG)
    monkeypatch.setattr(cli, "_make_client", lambda config: client)
    return SimpleNamespace(client=client, captured=captured)


def test_leads_builds_source_and_date_selector(stub):
    cli.event_leads(source="Easy Weddings", from_="2026-01-01", to="2026-12-31",
                    status=None, show_list=False, output_json=False)
    selector = stub.captured["selector"]
    assert selector["enquiryData.sourceId"] == "SRC_EASY"
    assert selector["enquiryData.date"]["$gte"] == to_ejson_date("2026-01-01")
    assert selector["enquiryData.date"]["$lt"] == to_ejson_date_end("2026-12-31")
    assert "status" not in selector


def test_leads_status_filter_in_selector(stub):
    cli.event_leads(source="SRC_EASY", from_=None, to=None, status=["Confirmed"],
                    show_list=False, output_json=False)
    assert stub.captured["selector"]["status"] == {"$in": [1]}
    assert "enquiryData.date" not in stub.captured["selector"]


def test_leads_count_line(stub, capsys):
    cli.event_leads(source="Easy Weddings", from_="2026-01-01", to="2026-12-31",
                    status=None, show_list=False, output_json=False)
    out = capsys.readouterr().out
    assert "3 lead(s) from Easy Weddings" in out
    assert "2026-01-01" in out and "2026-12-31" in out


def test_leads_json_shape(stub, capsys):
    cli.event_leads(source="Easy Weddings", from_="2026-01-01", to=None,
                    status=None, show_list=False, output_json=True)
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == "Easy Weddings"
    assert payload["source_id"] == "SRC_EASY"
    assert payload["count"] == 3
    assert payload["from"] == "2026-01-01"
    assert len(payload["leads"]) == 3
    assert payload["leads"][0]["event_id"] == "E1"
