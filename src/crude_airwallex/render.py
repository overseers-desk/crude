"""Local-time rendering helpers shared across the crude-airwallex CLI modules.

Every Airwallex resource carries ISO-8601 UTC timestamps; these render them in the
machine's local timezone (crude_common.localtime) for list columns and record
views, so the binary shows local time everywhere. Kept here, not in cli, so the
per-group cli_<group>.py modules can import them without an import cycle.
"""

from __future__ import annotations

from crude_common.localtime import format_local


def ts(field: str):
    """A list column callable rendering an ISO-8601 timestamp field in local time."""
    return lambda item: format_local(item.get(field))


def localize(item: dict, ts_fields) -> dict:
    """A copy of a record with the named ISO timestamp fields rendered in local time."""
    if not isinstance(item, dict):
        return item
    out = dict(item)
    for f in ts_fields:
        if out.get(f) is not None:
            out[f] = format_local(out[f])
    return out
