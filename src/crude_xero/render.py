"""How the crude-xero CLI modules show the dates Xero sends.

Xero sends a date as /Date(ms)/, .NET's JSON form of the millisecond UTC epoch,
and a timestamp as that or as ISO-8601. A table or a record view shows them
readably, and --json and LDIF output keep what Xero sent. A module of its own,
since every cli_<group>.py module prints through it.
"""

from __future__ import annotations

from crude_common import asof, output
from crude_common.config import site_timezone


def shown(key: str, value, *, offset: bool = False):
    """`value` as a table or a record view shows it.

    Under a key ending in UTC it is an instant, shown in the timezone the config
    names (the machine's when it names none). A record view prints the key
    beside the value, and the key says UTC, so there `offset` adds the offset.
    A /Date(ms)/ under any other key is a calendar date, which Xero sends as
    that day's UTC midnight, shown as the day. Anything else is unchanged.
    """
    if not isinstance(value, str):
        return value
    if key.endswith("UTC"):
        instant = asof.parse_stamp(value)
        if instant is None:
            return value
        local = instant.astimezone(site_timezone("xero"))
        if offset:
            return local.isoformat(sep=" ", timespec="minutes")
        return local.strftime("%Y-%m-%d %H:%M")
    if value.startswith("/Date("):
        instant = asof.parse_stamp(value)
        return instant.strftime("%Y-%m-%d") if instant else value
    return value


def emit_list(items, columns, what, output_json, **kwargs):
    """crude_common.output.emit_list, with a table's rows passed through `shown`."""
    if not output_json and kwargs.get("ldif") is None:
        items = [{k: shown(k, v) for k, v in item.items()} if isinstance(item, dict) else item
                 for item in items]
    output.emit_list(items, columns, what, output_json, **kwargs)


def emit_record(item, output_json, ldif=None):
    """crude_common.output.emit_record, with a record view's fields passed
    through `shown`."""
    if not output_json and ldif is None and isinstance(item, dict):
        item = {k: shown(k, v, offset=True) for k, v in item.items()}
    output.emit_record(item, output_json, ldif=ldif)
