"""crude-sonas EJSON date helpers render and encode in local time, not UTC.

Sonas stores an event date as venue-local midnight. Rendering it in UTC shows the
prior calendar day for any zone east of UTC (a Brisbane +10 wedding at local
midnight is the previous day in UTC), which made a 2026-06-18 wedding print as the
17th. These tests pin Australia/Brisbane so they are deterministic on any host.
"""

import os
import time

import pytest

from crude_sonas.client import date_str, to_ejson_date, to_ejson_date_end

# 2026-06-18 00:00 Australia/Brisbane (+10) == 2026-06-17 14:00 UTC.
BNE_2026_06_18_MIDNIGHT_MS = 1781704800000
# 2026-06-19 00:00 Brisbane: the exclusive upper bound for a --to 2026-06-18 filter.
BNE_2026_06_19_MIDNIGHT_MS = BNE_2026_06_18_MIDNIGHT_MS + 86400000


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


def test_date_str_renders_local_calendar_day(brisbane_tz):
    # The wedding stored at Brisbane midnight must read as the 18th, not the 17th.
    assert date_str({"$date": BNE_2026_06_18_MIDNIGHT_MS}) == "2026-06-18"


def test_to_ejson_date_encodes_local_midnight(brisbane_tz):
    assert to_ejson_date("2026-06-18") == {"$date": BNE_2026_06_18_MIDNIGHT_MS}


def test_typed_date_round_trips(brisbane_tz):
    assert date_str(to_ejson_date("2026-06-18")) == "2026-06-18"


def test_to_ejson_date_end_is_next_day_midnight(brisbane_tz):
    # The pubs match date < to, so --to 06-18 must encode 06-19 00:00 local to
    # include an event whose start date is 06-18 00:00 local.
    assert to_ejson_date_end("2026-06-18") == {"$date": BNE_2026_06_19_MIDNIGHT_MS}
    lo = to_ejson_date("2026-06-18")["$date"]
    hi = to_ejson_date_end("2026-06-18")["$date"]
    assert lo <= BNE_2026_06_18_MIDNIGHT_MS < hi


def test_date_str_passes_through_non_ejson():
    assert date_str(None) == ""
    assert date_str("already a string") == "already a string"
