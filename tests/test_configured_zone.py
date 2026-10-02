"""Unit tests for the rule that a site CLI works in the timezone its config names.

Each test puts the machine in one zone (Kolkata, UTC+5:30) and the config in
another (Brisbane, UTC+10), then checks that a typed date and a shown time
follow the config; with no zone in the config, the machine's applies.
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
