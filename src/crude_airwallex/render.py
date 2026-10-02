"""Local-time helpers shared across the crude-airwallex CLI modules.

Every Airwallex resource carries ISO-8601 UTC timestamps; these render them in
the account's timezone for list columns and record views, and `zone` hands the
per-group cli_<group>.py modules the same zone for the dates a user types, so the
binary works in one local time everywhere. Kept here, not in cli, so those
modules can import them without an import cycle.
"""

from __future__ import annotations

from crude_common.config import site_timezone
from crude_common.localtime import format_local


def zone():
    """The timezone the config names for the selected account, or None for the machine's."""
    return site_timezone("airwallex")


def ts(field: str):
    """A list column callable rendering an ISO-8601 timestamp field in local time."""
    tz = zone()
    return lambda item: format_local(item.get(field), tz=tz)


def localize(item: dict, ts_fields) -> dict:
    """A copy of a record with the named ISO timestamp fields rendered in local time."""
    if not isinstance(item, dict):
        return item
    tz = zone()
    out = dict(item)
    for f in ts_fields:
        if out.get(f) is not None:
            out[f] = format_local(out[f], tz=tz)
    return out
