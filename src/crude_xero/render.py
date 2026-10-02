"""How the crude-xero CLI modules show the dates Xero sends.

The Accounting and Payroll APIs send a date as /Date(ms)/, .NET's JSON form of
the millisecond UTC epoch. A table or a record view shows it readably, and
--json and LDIF output keep what Xero sent. Kept here, not in a cli module, so
the per-group cli_<group>.py modules can import it without an import cycle.
"""

from __future__ import annotations

import re

from crude_common import output
from crude_common.config import site_timezone
from crude_common.ldif import parse_epoch_ms
from crude_common.localtime import parse_iso_utc

# /Date(1672531200000+0000)/: the digits are the millisecond UTC epoch, the
# trailing offset is presentation only.
_DOTNET_DATE = re.compile(r"^/Date\((-?\d+)(?:[+-]\d{4})?\)/$")


def parse_xero_dt(value):
    """Parse a Xero timestamp that may arrive as /Date(ms)/ or as ISO-8601.

    Different Accounting endpoints report UpdatedDateUTC in either form, so a
    /Date(ms)/ value is unwrapped to its epoch milliseconds and anything else
    falls through to the ISO parser.
    """
    if isinstance(value, str):
        m = _DOTNET_DATE.match(value.strip())
        if m:
            return parse_epoch_ms(m.group(1))
    return parse_iso_utc(value)


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
        instant = parse_xero_dt(value)
        if instant is None:
            return value
        local = instant.astimezone(site_timezone("xero"))
        if offset:
            return local.isoformat(sep=" ", timespec="minutes")
        return local.strftime("%Y-%m-%d %H:%M")
    m = _DOTNET_DATE.match(value.strip())
    return parse_epoch_ms(m.group(1)).strftime("%Y-%m-%d") if m else value


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
