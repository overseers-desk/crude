"""Local-time helpers shared across the crude-airwallex CLI modules.

Every Airwallex resource carries ISO-8601 UTC timestamps; these render them in
the timezone the config names for list columns and record views, and `zone` hands
the per-group cli_<group>.py modules the same zone for the dates a user types.
Kept here, not in cli, so those modules can import them without an import cycle.
"""

from __future__ import annotations

from crude_common import localtime
from crude_common.config import site_timezone
from crude_common.localtime import format_local


def zone():
    return site_timezone("airwallex")


def ts(field: str):
    """A list column callable rendering an ISO-8601 timestamp field in local time."""
    return lambda item: format_local(item.get(field), tz=zone())


def localize(item: dict, ts_fields) -> dict:
    """A copy of a record with the named ISO timestamp fields rendered in local time."""
    return localtime.localize(item, ts_fields, tz=zone())
