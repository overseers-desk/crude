"""Unit tests for the rule that a site CLI works in the timezone its config names.

Each test puts the machine in one zone (Kolkata, UTC+5:30) and the config in
another (Brisbane, UTC+10), then checks that a typed date and a shown time
follow the config. With no zone in the config the machine's applies, except to
a Sonas clock time, which stays UTC.
"""

import os
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from crude_common import config

runner = CliRunner()

# Brisbane midnight on 3 October 2026, as the instant the APIs carry.
BNE_MIDNIGHT_UTC = "2026-10-02T14:00:00Z"
BNE_MIDNIGHT_MS = int(datetime(2026, 10, 2, 14, tzinfo=timezone.utc).timestamp() * 1000)
BRISBANE = {"timezone": "Australia/Brisbane"}


@pytest.fixture
def kolkata_machine():
    old = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Kolkata"
    time.tzset()
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


def _on_disk(monkeypatch, cfg):
    """Make `cfg` the config on disk for the timezone lookup."""
    monkeypatch.setattr(config, "find_config", lambda: "config.toml")
    monkeypatch.setattr(config, "read_config", lambda _p: cfg)
    config._timezone_named_for.cache_clear()


def test_site_timezone_prefers_the_site_key_and_is_none_when_unnamed(monkeypatch):
    _on_disk(monkeypatch, {"timezone": "UTC", "wise": {"timezone": "Australia/Brisbane"}})
    assert str(config.site_timezone("wise")) == "Australia/Brisbane"
    assert str(config.site_timezone("airwallex")) == "UTC"
    _on_disk(monkeypatch, {})
    assert config.site_timezone("wise") is None


# ----------------------------------------------------------------------
# Airwallex
# ----------------------------------------------------------------------


def _airwallex_bounds(monkeypatch):
    """Run `transaction list` for 3 October; return the bounds the client is given."""
    from crude_airwallex import cli_core

    seen = {}
    core = SimpleNamespace(
        list_financial_transactions=lambda **kw: seen.update(kw) or [])
    monkeypatch.setattr(cli_core, "_client", lambda: SimpleNamespace(core=core))
    result = runner.invoke(
        cli_core.txn_app, ["list", "--from", "2026-10-03", "--to", "2026-10-03", "--json"])
    assert result.exit_code == 0
    return seen["from_"], seen["to"]


def test_airwallex_reads_a_typed_day_in_the_configured_zone(kolkata_machine, monkeypatch):
    _on_disk(monkeypatch, BRISBANE)
    assert _airwallex_bounds(monkeypatch) == (BNE_MIDNIGHT_UTC, "2026-10-03T14:00:00Z")


def test_airwallex_falls_back_to_the_machine_zone(kolkata_machine, monkeypatch):
    _on_disk(monkeypatch, {})
    # Kolkata midnight is 18:30Z the day before.
    assert _airwallex_bounds(monkeypatch)[0] == "2026-10-02T18:30:00Z"


def test_airwallex_shows_times_in_the_configured_zone(kolkata_machine, monkeypatch):
    from crude_airwallex import render

    _on_disk(monkeypatch, BRISBANE)
    assert render.ts("createdAt")({"createdAt": BNE_MIDNIGHT_UTC}) == "2026-10-03 00:00"
    shown = render.localize({"created_at": BNE_MIDNIGHT_UTC}, ("created_at",))
    assert shown["created_at"] == "2026-10-03 00:00"


# ----------------------------------------------------------------------
# Wise
# ----------------------------------------------------------------------


def test_wise_reads_a_typed_day_and_shows_times_in_the_configured_zone(
        kolkata_machine, monkeypatch):
    from crude_wise import cli_resources as wise

    _on_disk(monkeypatch, BRISBANE)
    # A past day, since the window's end is never later than now.
    assert wise._window("2026-06-01", "2026-06-01") == (
        "2026-05-31T14:00:00Z", "2026-06-01T14:00:00Z")
    assert wise._ts("createdAt")({"createdAt": BNE_MIDNIGHT_UTC}) == "2026-10-03 00:00"


def test_wise_falls_back_to_the_machine_zone(kolkata_machine, monkeypatch):
    from crude_wise import cli_resources as wise

    _on_disk(monkeypatch, {})
    assert wise._window("2026-06-01", "2026-06-01")[0] == "2026-05-31T18:30:00Z"
    assert wise._ts("createdAt")({"createdAt": BNE_MIDNIGHT_UTC}) == "2026-10-02 19:30"


def test_wise_record_views_show_their_times_as_the_lists_do(kolkata_machine, monkeypatch):
    from crude_wise import cli, cli_resources

    transfer = {"id": 9, "status": "outgoing_payment_sent", "created": "2026-10-02 14:00:00"}
    profile = {"id": 3, "type": "BUSINESS", "createdAt": BNE_MIDNIGHT_UTC, "updatedAt": None}
    sess = SimpleNamespace(get=lambda _path: transfer, profile=lambda _id: profile)
    monkeypatch.setattr(cli_resources, "_session", lambda: sess)
    _on_disk(monkeypatch, BRISBANE)
    for argv, raw in ((["transfer", "get", "9"], "14:00:00"),
                      (["profile", "get", "3"], BNE_MIDNIGHT_UTC)):
        table = runner.invoke(cli.app, argv)
        assert table.exit_code == 0, table.output
        assert "2026-10-03 00:00" in table.output and raw not in table.output
        assert raw in runner.invoke(cli.app, argv + ["--json"]).output


# ----------------------------------------------------------------------
# Clover
# ----------------------------------------------------------------------


def test_clover_shows_a_record_time_in_the_configured_zone(kolkata_machine, monkeypatch):
    from crude_clover import resources

    column = resources.ms_local("createdTime")
    _on_disk(monkeypatch, BRISBANE)
    assert column({"createdTime": BNE_MIDNIGHT_MS}) == "2026-10-03 00:00"
    _on_disk(monkeypatch, {})
    assert column({"createdTime": BNE_MIDNIGHT_MS}) == "2026-10-02 19:30"
    assert column({}) == ""


# ----------------------------------------------------------------------
# Sonas
# ----------------------------------------------------------------------


def test_sonas_event_list_reads_and_shows_days_in_the_venue_zone(kolkata_machine, monkeypatch):
    from crude_sonas import cli

    _on_disk(monkeypatch, {"sonas": {"timezone": "Australia/Brisbane"}})
    seen = {}

    def list_events(from_, to, tz):
        seen.update(from_=from_, to=to, tz=str(tz))
        return [{"_id": "E1", "date": {"$date": BNE_MIDNIGHT_MS}}]

    client = SimpleNamespace(list_events=list_events, close=lambda: None)
    monkeypatch.setattr(cli, "find_config", lambda: "config.toml")
    monkeypatch.setattr(cli, "read_config", lambda _p: {})
    monkeypatch.setattr(cli, "_make_client", lambda _c: client)
    result = runner.invoke(cli.app, ["event", "list", "--from", "2026-10-03", "--to", "2026-10-03"])
    assert result.exit_code == 0
    assert seen == {"from_": "2026-10-03", "to": "2026-10-03", "tz": "Australia/Brisbane"}
    # Stored at Brisbane midnight on the 3rd; Kolkata's own clock would say the 2nd.
    assert "2026-10-03" in result.output and "2026-10-02" not in result.output


def test_sonas_reads_a_time_with_no_offset_as_venue_time(kolkata_machine, monkeypatch):
    from crude_sonas import cli

    def ms(*utc):
        return int(datetime(*utc, tzinfo=timezone.utc).timestamp() * 1000)

    _on_disk(monkeypatch, BRISBANE)
    # 3 pm in Brisbane is 05:00 UTC.
    assert cli._datetime_ejson("2031-11-20T15:00") == {"$date": ms(2031, 11, 20, 5)}
    assert cli._datetime_ejson("2031-11-20T15:00+02:00") == {"$date": ms(2031, 11, 20, 13)}
    _on_disk(monkeypatch, {})
    assert cli._datetime_ejson("2031-11-20T15:00") == {"$date": ms(2031, 11, 20, 15)}


def test_site_timezone_reads_the_config_once_per_site(monkeypatch):
    reads = []
    monkeypatch.setattr(config, "find_config", lambda: "config.toml")
    monkeypatch.setattr(config, "read_config", lambda _p: reads.append(1) or BRISBANE)
    config._timezone_named_for.cache_clear()
    for _ in range(3):
        config.site_timezone("wise")
    config.site_timezone("clover")
    assert len(reads) == 2


# ----------------------------------------------------------------------
# Mautic and Facebook
# ----------------------------------------------------------------------


def test_mautic_shows_a_timestamp_in_the_configured_zone(kolkata_machine, monkeypatch):
    from crude_mautic import cli_resources as mautic

    added = mautic._when("dateAdded")
    _on_disk(monkeypatch, BRISBANE)
    assert added({"dateAdded": "2026-10-02T14:00:00+00:00"}) == "2026-10-03 00:00"
    _on_disk(monkeypatch, {})
    assert added({"dateAdded": "2026-10-02T14:00:00+00:00"}) == "2026-10-02 19:30"
    assert added({}) == ""


def test_mautic_contact_shows_last_active_once_in_the_configured_zone(
        kolkata_machine, monkeypatch):
    from crude_mautic import cli, cli_resources

    contact = {
        "id": 7, "points": 0,
        "dateAdded": "2026-10-01T02:00:00+00:00", "lastActive": "2026-10-02T14:00:00+00:00",
        # Mautic repeats last_active among the contact's own fields, as stored.
        "fields": {"all": {"id": 7, "email": "a@example.com",
                           "last_active": "2026-10-02 14:00:00"}},
    }
    sess = SimpleNamespace(one=lambda _path, _entity: contact)
    monkeypatch.setattr(cli_resources, "_session", lambda: sess)
    _on_disk(monkeypatch, BRISBANE)
    result = runner.invoke(cli.app, ["contact", "get", "7"])
    assert result.exit_code == 0, result.output
    assert "2026-10-03 00:00" in result.output      # last active, Brisbane
    assert "2026-10-01 12:00" in result.output      # added, Brisbane
    assert "14:00:00" not in result.output
    assert "a@example.com" in result.output


def test_mautic_record_view_shows_its_timestamps_in_the_configured_zone(
        kolkata_machine, monkeypatch):
    from crude_mautic import cli, cli_resources

    email = {"id": 7, "name": "Spring", "dateAdded": "2026-10-02T14:00:00+00:00",
             "dateModified": None, "publishUp": None}
    sess = SimpleNamespace(one=lambda _path, _entity: email)
    monkeypatch.setattr(cli_resources, "_session", lambda: sess)
    _on_disk(monkeypatch, BRISBANE)
    table = runner.invoke(cli.app, ["email", "get", "7"])
    assert table.exit_code == 0, table.output
    assert "2026-10-03 00:00" in table.output
    raw = runner.invoke(cli.app, ["email", "get", "7", "--json"])
    assert "2026-10-02T14:00:00+00:00" in raw.output


def test_facebook_shows_and_schedules_in_the_configured_zone(kolkata_machine, monkeypatch):
    from crude_facebook import cli_resources as facebook

    def unix(day, hour, minute=0):
        return str(int(datetime(2026, 10, day, hour, minute, tzinfo=timezone.utc).timestamp()))

    _on_disk(monkeypatch, BRISBANE)
    assert facebook._when("created_time")({"created_time": "2026-10-02T14:00:00+0000"}) == (
        "2026-10-03 00:00")
    # 09:00 on 5 October in Brisbane is 23:00 UTC on the 4th.
    assert facebook._schedule_time("2026-10-05T09:00") == unix(4, 23)
    assert facebook._schedule_time("2026-10-05T01:00:00+02:00") == unix(4, 23)
    assert facebook._schedule_time(unix(4, 23)) == unix(4, 23)
    assert facebook._schedule_time("next monday") == "next monday"
    _on_disk(monkeypatch, {})
    # With no zone in the config the machine's applies: 09:00 in Kolkata is 03:30 UTC.
    assert facebook._schedule_time("2026-10-05T09:00") == unix(5, 3, 30)


def test_clover_record_view_shows_its_times_as_the_list_does(kolkata_machine, monkeypatch):
    from crude_clover import cli, cli_resources

    payment = {"id": "P1", "amount": 5000, "createdTime": BNE_MIDNIGHT_MS, "modifiedTime": None}
    stub = SimpleNamespace(resources=SimpleNamespace(get=lambda *a, **kw: payment))
    monkeypatch.setattr(cli_resources, "_client", lambda: stub)
    _on_disk(monkeypatch, BRISBANE)
    table = runner.invoke(cli.app, ["payments", "get", "P1"])
    assert table.exit_code == 0, table.output
    assert "2026-10-03 00:00" in table.output
    raw = runner.invoke(cli.app, ["payments", "get", "P1", "--json"])
    assert str(BNE_MIDNIGHT_MS) in raw.output


# ----------------------------------------------------------------------
# Rezdy record views and vouchers
# ----------------------------------------------------------------------


@pytest.fixture
def rezdy(monkeypatch):
    """crude-rezdy with a Brisbane account and no network; returns the client class."""
    from crude_rezdy import cli
    from crude_rezdy.client import RezdyClient

    monkeypatch.setattr(cli, "find_config", lambda: "config.toml")
    monkeypatch.setattr(
        cli, "read_config",
        lambda _p: {"rezdy": {"api_key": "KEY", "timezone": "Australia/Brisbane"}})
    monkeypatch.setattr(cli, "_make_client", lambda _c: RezdyClient("KEY"))
    return RezdyClient


def test_rezdy_booking_view_shows_its_instants_in_the_account_zone(
        kolkata_machine, rezdy, monkeypatch):
    from crude_rezdy import cli

    booking = {"orderNumber": "R1", "dateCreated": BNE_MIDNIGHT_UTC, "datePaid": None}
    monkeypatch.setattr(rezdy, "get_booking", lambda self, order: booking)
    table = runner.invoke(cli.app, ["booking", "get", "R1"])
    assert table.exit_code == 0, table.output
    assert "2026-10-03 00:00" in table.output
    raw = runner.invoke(cli.app, ["booking", "get", "R1", "--json"])
    assert BNE_MIDNIGHT_UTC in raw.output


def test_rezdy_voucher_dates_are_the_account_days(kolkata_machine, rezdy, monkeypatch):
    from crude_rezdy import cli

    # Issued at the start of 3 October in Brisbane, valid to the end of the 4th.
    voucher = {"code": "V1", "status": "ISSUED", "issueDate": BNE_MIDNIGHT_UTC,
               "expiryDate": "2026-10-04T13:59:59Z"}
    monkeypatch.setattr(rezdy, "list_vouchers", lambda self, **kw: [voucher])
    monkeypatch.setattr(rezdy, "get_voucher", lambda self, code: voucher)
    listed = runner.invoke(cli.app, ["voucher", "list"])
    assert listed.exit_code == 0, listed.output
    assert "2026-10-03 00:00" in listed.output and "2026-10-04 23:59" in listed.output
    one = runner.invoke(cli.app, ["voucher", "get", "V1"])
    assert "2026-10-03 00:00" in one.output and "2026-10-04 23:59" in one.output


# ----------------------------------------------------------------------
# Deputy shift times
# ----------------------------------------------------------------------


def test_deputy_shows_shift_times_in_the_configured_zone(kolkata_machine, monkeypatch):
    from rich.console import Console

    from crude_deputy import cli

    # 08:00 to 15:30 on 3 October in Brisbane, as the Unix seconds Deputy stores.
    start = int(datetime(2026, 10, 2, 22, tzinfo=timezone.utc).timestamp())
    shift = {"Id": 1, "Date": "2026-10-03T00:00:00+10:00", "StartTime": start,
             "EndTime": start + 27000, "OperationalUnit": 4, "Employee": 73}
    client = SimpleNamespace(query_resource=lambda *a, **kw: [shift],
                             get_resource=lambda obj, id: shift)
    monkeypatch.setattr(cli, "find_config", lambda: "config.toml")
    monkeypatch.setattr(cli, "read_config", lambda _p: {})
    monkeypatch.setattr(cli, "_make_client", lambda _c: client)
    # Wide enough that the table truncates no cell.
    monkeypatch.setattr(cli, "console", Console(width=200))
    _on_disk(monkeypatch, BRISBANE)
    listed = runner.invoke(cli.app, ["roster", "list"])
    assert listed.exit_code == 0, listed.output
    assert "2026-10-03 08:00" in listed.output and "2026-10-03 15:30" in listed.output
    one = runner.invoke(cli.app, ["roster", "get", "1"])
    assert "2026-10-03 08:00" in one.output
    raw = runner.invoke(cli.app, ["roster", "get", "1", "--json"])
    assert str(start) in raw.output


# ----------------------------------------------------------------------
# ATDW, Xero and Skål
# ----------------------------------------------------------------------


def test_atdw_listing_view_shows_its_stamps_in_the_configured_zone(kolkata_machine, monkeypatch):
    from crude_atdw import cli

    listing = {"id": "L1", "name": "Cafe", "publishedOn": "2026-10-02T14:00:00.605Z",
               "updatedOn": None}
    client = SimpleNamespace(get_own_listing=lambda _id: listing)
    monkeypatch.setattr(cli, "find_config", lambda: "config.toml")
    monkeypatch.setattr(cli, "read_config", lambda _p: {})
    monkeypatch.setattr(cli, "_make_client", lambda _c: client)
    _on_disk(monkeypatch, BRISBANE)
    table = runner.invoke(cli.app, ["listing", "get", "L1"])
    assert table.exit_code == 0, table.output
    assert "2026-10-03 00:00" in table.output
    raw = runner.invoke(cli.app, ["listing", "get", "L1", "--json"])
    assert "2026-10-02T14:00:00.605Z" in raw.output
