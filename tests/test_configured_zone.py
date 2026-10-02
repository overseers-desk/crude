"""Unit tests for the rule that a site CLI works in the timezone its config names.

Each test puts the machine in one zone (Kolkata, UTC+5:30) and the config in
another (Brisbane, UTC+10), then checks that a typed date and a shown time
follow the config; with no zone in the config, the machine's applies.
"""

import os
import time
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

runner = CliRunner()

# Brisbane midnight on 3 October 2026, as the instant the APIs carry.
BNE_MIDNIGHT_UTC = "2026-10-02T14:00:00Z"


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


def _config(monkeypatch, module, cfg):
    """Make `module` read `cfg` as the config on disk."""
    monkeypatch.setattr(module, "find_config", lambda: "config.toml")
    monkeypatch.setattr(module, "read_config", lambda _p: cfg)


# ----------------------------------------------------------------------
# Airwallex
# ----------------------------------------------------------------------


def _airwallex_transactions(monkeypatch, cfg):
    """Run `transaction list` for 3 October against `cfg`; return the bounds sent."""
    from crude_airwallex import cli_core, render

    _config(monkeypatch, render, cfg)
    seen = {}
    core = SimpleNamespace(
        list_financial_transactions=lambda **kw: seen.update(kw) or [])
    monkeypatch.setattr(cli_core, "_client", lambda: SimpleNamespace(core=core))
    result = runner.invoke(
        cli_core.txn_app, ["list", "--from", "2026-10-03", "--to", "2026-10-03", "--json"])
    assert result.exit_code == 0
    return seen["from_"], seen["to"]


def test_airwallex_reads_a_typed_day_in_the_configured_zone(kolkata_machine, monkeypatch):
    bounds = _airwallex_transactions(monkeypatch, {"timezone": "Australia/Brisbane"})
    assert bounds == (BNE_MIDNIGHT_UTC, "2026-10-03T14:00:00Z")


def test_airwallex_site_zone_wins_over_the_top_level_one(kolkata_machine, monkeypatch):
    cfg = {"timezone": "UTC", "airwallex": {"timezone": "Australia/Brisbane"}}
    assert _airwallex_transactions(monkeypatch, cfg)[0] == BNE_MIDNIGHT_UTC


def test_airwallex_falls_back_to_the_machine_zone(kolkata_machine, monkeypatch):
    # Kolkata midnight is 18:30Z the day before.
    assert _airwallex_transactions(monkeypatch, {})[0] == "2026-10-02T18:30:00Z"


def test_airwallex_shows_times_in_the_configured_zone(kolkata_machine, monkeypatch):
    from crude_airwallex import render

    _config(monkeypatch, render, {"timezone": "Australia/Brisbane"})
    assert render.ts("createdAt")({"createdAt": BNE_MIDNIGHT_UTC}) == "2026-10-03 00:00"
    shown = render.localize({"created_at": BNE_MIDNIGHT_UTC}, ("created_at",))
    assert shown["created_at"] == "2026-10-03 00:00"
