"""Unit tests for how crude-rezdy's list filters reach the Supplier API.

The commands run through a CliRunner against a real client whose transport is
stubbed, so each test sees the query parameters Rezdy would receive and nothing
reaches the network.
"""

from types import SimpleNamespace

import json
from zoneinfo import ZoneInfo

import pytest
from typer.testing import CliRunner

from crude_rezdy import cli
from crude_rezdy.client import RezdyClient

runner = CliRunner()


@pytest.fixture
def served():
    """The records the stubbed API answers with; a test fills it before invoking."""
    return []


@pytest.fixture
def sent(monkeypatch, served):
    """The requests a command sends, each as its method, url and query parameters."""
    client = RezdyClient("KEY")
    calls = []

    def fake_request(method, url, params=None, json=None):
        calls.append({"method": method, "url": url, "params": params})
        body = {"requestStatus": {"success": True}, "items": list(served)}
        return SimpleNamespace(ok=True, status_code=200, json=lambda: body)

    monkeypatch.setattr(client.session, "request", fake_request)
    monkeypatch.setattr(cli, "find_config", lambda: "config.toml")
    monkeypatch.setattr(
        cli, "read_config",
        lambda _p: {"rezdy": {"api_key": "KEY", "timezone": "Australia/Brisbane"}},
    )
    monkeypatch.setattr(cli, "_make_client", lambda _c: client)
    return calls


def test_booking_list_sends_the_agent_filters(sent):
    result = runner.invoke(cli.app, [
        "booking", "list", "--source-channel", "REDBALLOON",
        "--reseller-reference", "410072225", "--role", "supplier", "--json",
    ])
    assert result.exit_code == 0
    params = sent[0]["params"]
    assert params["sourceChannel"] == "REDBALLOON"
    assert params["resellerReference"] == "410072225"
    assert params["role"] == "SUPPLIER"


def test_booking_list_repeats_product_for_several_codes(sent):
    result = runner.invoke(cli.app, [
        "booking", "list", "--product", "P1", "--product", "P2", "--json",
    ])
    assert result.exit_code == 0
    # A list value goes out as one productCode parameter per code.
    assert sent[0]["params"]["productCode"] == ["P1", "P2"]


def test_booking_list_rejects_an_unknown_role(sent):
    result = runner.invoke(cli.app, ["booking", "list", "--role", "bogus"])
    assert result.exit_code == 1
    assert sent == []


def test_category_list_filters_by_name_and_visibility(sent):
    runner.invoke(cli.app, ["category", "list", "--search", "horse", "--private", "--json"])
    assert sent[0]["params"]["search"] == "horse"
    assert sent[0]["params"]["visible"] == "false"
    runner.invoke(cli.app, ["category", "list", "--json"])
    assert "visible" not in sent[1]["params"]   # both kinds when the flag is omitted


def test_resource_sessions_reads_a_bare_date_as_the_whole_local_day(sent):
    runner.invoke(cli.app, [
        "resource", "sessions", "103494", "--from", "2026-10-05", "--to", "2026-10-05",
        "--offset", "100", "--json",
    ])
    assert sent[0]["url"].endswith("/v1/resources/103494/sessions")
    params = sent[0]["params"]
    assert params["startTimeLocal"] == "2026-10-05 00:00:00"
    assert params["endTimeLocal"] == "2026-10-05 23:59:59"
    assert params["offset"] == 100


def test_availability_list_pages_with_offset(sent):
    runner.invoke(cli.app, [
        "availability", "list", "--product", "P1", "--from", "2026-10-02 00:00:00",
        "--to", "2026-10-09 23:59:59", "--limit", "1000", "--offset", "1000", "--json",
    ])
    assert sent[0]["params"]["offset"] == 1000


def test_booking_list_sends_a_typed_day_as_the_account_day(sent):
    result = runner.invoke(cli.app, [
        "booking", "list", "--from", "2026-10-03", "--to", "2026-10-03",
        "--created-from", "2026-09-30", "--created-to", "2026-09-30", "--json",
    ])
    assert result.exit_code == 0
    params = sent[0]["params"]
    # Brisbane is UTC+10: the day's first and last second, as UTC instants.
    assert params["minTourStartTime"] == "2026-10-02T14:00:00Z"
    assert params["maxTourStartTime"] == "2026-10-03T13:59:59Z"
    assert params["minDateCreated"] == "2026-09-29T14:00:00Z"
    assert params["maxDateCreated"] == "2026-09-30T13:59:59Z"


def test_booking_list_refuses_a_malformed_date_before_calling(sent):
    result = runner.invoke(cli.app, ["booking", "list", "--from", "03/10/2026"])
    assert result.exit_code == 1
    assert sent == []


def test_updated_from_goes_to_rezdy_a_second_early(sent):
    result = runner.invoke(cli.app, ["booking", "list", "--updated-from", "2026-09-25", "--json"])
    assert result.exit_code == 0
    # Rezdy's updatedSince leaves out a booking stamped exactly at its value, and
    # the Brisbane day starts at 14:00:00Z: a second earlier keeps that booking.
    assert sent[0]["params"]["updatedSince"] == "2026-09-24T13:59:59Z"


def test_a_never_updated_booking_is_in_no_update_window(sent, served):
    served.extend([
        {"orderNumber": "R-OLD", "dateUpdated": "2024-12-31T00:00:00Z"},
        {"orderNumber": "R-NEVER"},
    ])
    result = runner.invoke(cli.app, ["booking", "list", "--updated-to", "2025-01-01", "--json"])
    assert [b["orderNumber"] for b in json.loads(result.output)] == ["R-OLD"]


def test_cancellations_from_is_filtered_by_rezdy(sent):
    result = runner.invoke(cli.app, ["booking", "cancellations", "--from", "2026-09-01", "--json"])
    assert result.exit_code == 0
    params = sent[0]["params"]
    assert params["orderStatus"] == "CANCELLED"
    assert params["updatedSince"] == "2026-08-31T13:59:59Z"


def test_update_column_shows_the_account_local_day():
    tz = ZoneInfo("Australia/Brisbane")
    # 21:23Z on 2 May is 07:23 on 3 May in Brisbane.
    assert cli._local_day("2026-05-02T21:23:28Z", tz) == "2026-05-03"
    assert cli._local_day(None, tz) == ""
