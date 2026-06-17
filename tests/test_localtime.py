"""crude_common.localtime renders ISO-8601 UTC in the machine's local zone.

A REST API returns timestamps in UTC; showing them raw prints the wrong calendar
day for any zone east of UTC (a 2026-06-18 00:00 +10 instant is 2026-06-17 14:00
UTC). These tests pin Australia/Brisbane so they are deterministic on any host,
the same discipline as tests/test_sonas_dates.py.
"""

import os
import time

import pytest

from crude_common.localtime import format_local, parse_iso_utc, to_utc_iso


@pytest.fixture
def brisbane_tz():
    old = os.environ.get("TZ")
    os.environ["TZ"] = "Australia/Brisbane"
    time.tzset()
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


def test_format_local_renders_local_calendar_day(brisbane_tz):
    # The +10 zone turns a 14:00 UTC instant into 00:00 the next calendar day.
    assert format_local("2026-06-17T14:00:00Z") == "2026-06-18 00:00"


def test_format_local_z_and_explicit_offset_are_equivalent(brisbane_tz):
    assert format_local("2026-06-17T14:00:00+00:00") == format_local("2026-06-17T14:00:00Z")


def test_format_local_offset_without_colon(brisbane_tz):
    # Airwallex may render the offset as +0000; fromisoformat needs +00:00 pre-3.11.
    assert format_local("2026-06-17T14:00:00+0000") == "2026-06-18 00:00"


def test_format_local_naive_is_assumed_utc(brisbane_tz):
    assert format_local("2026-06-17T14:00:00") == "2026-06-18 00:00"


def test_to_utc_iso_is_start_of_local_day(brisbane_tz):
    assert to_utc_iso("2026-06-18") == "2026-06-17T14:00:00Z"


def test_to_utc_iso_end_is_exclusive_next_local_midnight(brisbane_tz):
    # Half-open [from, to): --to 06-18 must cover all of the 18th, so the bound is
    # 06-19 00:00 local == 06-18 14:00 UTC. (Flip if a live check shows the
    # Airwallex to_created_at is inclusive.)
    assert to_utc_iso("2026-06-18", end=True) == "2026-06-18T14:00:00Z"


def test_typed_date_round_trips(brisbane_tz):
    assert format_local(to_utc_iso("2026-06-18"))[:10] == "2026-06-18"


def test_format_local_passes_through_non_timestamps():
    assert format_local(None) == ""
    assert format_local("already a string") == "already a string"
    assert format_local(42) == "42"


def test_to_utc_iso_passes_through_a_full_timestamp():
    # A value already carrying a time (length != 10) is returned unchanged.
    assert to_utc_iso("2026-06-18T05:00:00Z") == "2026-06-18T05:00:00Z"


def test_parse_iso_utc_returns_aware_utc():
    dt = parse_iso_utc("2026-06-17T14:00:00Z")
    assert dt is not None and dt.utcoffset().total_seconds() == 0
    assert parse_iso_utc("not a date") is None
    assert parse_iso_utc(None) is None
