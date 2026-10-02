"""Unit tests for multi-account resolution and rezdy's timezone day-bounds.

These are pure functions with no network, so they run in the default (non-live)
suite. They pin the behaviours the features hinge on: the scalar-vs-subtable
split that lets one site section hold several accounts, and the reading of a
typed operational day in the account's zone, not UTC.
"""

import typer
import pytest

from crude_common.config import resolve_account
from crude_rezdy.cli import _bound_utc
from zoneinfo import ZoneInfo


CONFIG = {
    "rezdy": {
        "api_key": "AU-key",
        "environment": "production",
        "timezone": "Australia/Brisbane",
        "es": {"api_key": "ES-key", "timezone": "Europe/Madrid"},
    }
}


def test_default_account_is_scalar_keys_only():
    assert resolve_account(CONFIG, "rezdy", None) == {
        "api_key": "AU-key",
        "environment": "production",
        "timezone": "Australia/Brisbane",
    }


def test_named_account_returns_its_subtable():
    assert resolve_account(CONFIG, "rezdy", "es") == {
        "api_key": "ES-key",
        "timezone": "Europe/Madrid",
    }


def test_unknown_account_exits():
    with pytest.raises(typer.Exit):
        resolve_account(CONFIG, "rezdy", "nope")


def test_field_name_is_not_an_account():
    # api_key is a scalar default-account field, never selectable as an account.
    with pytest.raises(typer.Exit):
        resolve_account(CONFIG, "rezdy", "api_key")


def test_missing_section_default_is_empty():
    assert resolve_account({}, "rezdy", None) == {}


def test_bound_reads_typed_date_in_account_zone():
    # The real cancelled booking from issue #3: 21:23Z is 07:23 next day in Brisbane.
    date_updated = "2026-05-02T21:23:28Z"
    tz = ZoneInfo("Australia/Brisbane")
    # Filed under 03 May Brisbane, so --to 2026-05-02 must exclude it...
    assert not (date_updated <= _bound_utc("2026-05-02", tz, end=True))
    # ...and --to 2026-05-03 must include it.
    assert date_updated <= _bound_utc("2026-05-03", tz, end=True)


def test_bound_spans_the_whole_local_day():
    # A Brisbane day runs 14:00Z the day before to 13:59:59Z: sent raw, the bare
    # date would be midnight UTC, 10:00 local, and miss the morning.
    tz = ZoneInfo("Australia/Brisbane")
    assert _bound_utc("2026-10-03", tz, end=False) == "2026-10-02T14:00:00Z"
    assert _bound_utc("2026-10-03", tz, end=True) == "2026-10-03T13:59:59Z"


def test_bound_keeps_an_instant_and_converts_an_offset():
    tz = ZoneInfo("Australia/Brisbane")
    assert _bound_utc("2026-05-02T10:00:00Z", tz, end=False) == "2026-05-02T10:00:00Z"
    assert _bound_utc("2026-05-03T07:23:28+10:00", tz, end=False) == "2026-05-02T21:23:28Z"
    # A time carrying neither Z nor an offset is account-local, T or space
    # separated: 10:00 in Brisbane is midnight UTC.
    assert _bound_utc("2026-05-02T10:00:00", tz, end=False) == "2026-05-02T00:00:00Z"
    assert _bound_utc("2026-05-02 10:00:00", tz, end=False) == "2026-05-02T00:00:00Z"


def test_bound_never_reads_an_offset_as_account_local():
    tz = ZoneInfo("Australia/Brisbane")
    # "-05" is an offset fromisoformat accepts from Python 3.11 and rejects before
    # it. Honoured or refused, it is not 10:00 in Brisbane.
    try:
        got = _bound_utc("2026-05-02T10:00:00-05", tz, end=False)
    except typer.Exit:
        return
    assert got == "2026-05-02T15:00:00Z"


@pytest.mark.parametrize("typed", ["03/10/2026", " ", "20261003", "2026-13-40"])
def test_bound_rejects_a_value_that_is_no_date(typed, capsys):
    with pytest.raises(typer.Exit):
        _bound_utc(typed, ZoneInfo("Australia/Brisbane"), end=True, flag="--to")
    assert "--to" in capsys.readouterr().err


def test_make_client_requires_timezone():
    # timezone is a required rezdy field, enforced for any command, not just the
    # date filters: a key-only account is a config error.
    from crude_rezdy.cli import _make_client
    with pytest.raises(typer.Exit):
        _make_client({"rezdy": {"api_key": "AU-key"}})


def test_make_client_rejects_unknown_timezone():
    from crude_rezdy.cli import _make_client
    with pytest.raises(typer.Exit):
        _make_client({"rezdy": {"api_key": "AU-key", "timezone": "Mars/Phobos"}})
