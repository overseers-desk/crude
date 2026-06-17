"""ISO-8601-UTC to system-local-time conversion for the crude site CLIs.

REST APIs return timestamps as ISO-8601 UTC; a CLI run on a user's machine should
show them in that machine's local timezone, and read typed --from/--to dates in
local time too. This is the dominant wire-format conversion, so it lives here for
reuse rather than in any one binary. The two pre-existing local-time helpers keep
their own bespoke code: crude_sonas works in EJSON epoch-ms ({"$date": ms}) and
crude_rezdy in a config-supplied IANA zone, neither of which is this ISO/system
case; leaving them untouched preserves their tested boundary conventions.

The system local zone is read from the process environment at call time: a naive
datetime's .astimezone() with no argument is interpreted in it, and converting to
it is also .astimezone() with no argument. Nothing is configured; "local to the
user's computer" is exactly the machine zone.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

# A trailing numeric offset with no colon (+0000), which datetime.fromisoformat
# rejects before Python 3.11; rewritten to +00:00.
_OFFSET_NO_COLON = re.compile(r"([+-]\d{2})(\d{2})$")


def parse_iso_utc(value):
    """Parse an ISO-8601 instant to an aware UTC datetime, or None if unparseable.

    A trailing 'Z' is normalised to '+00:00' and a colon is inserted into a bare
    +HHMM offset (datetime.fromisoformat rejects both before Python 3.11, and the
    project targets 3.9+). A parsed value carrying no tzinfo is assumed UTC, because
    the APIs document their timestamps as UTC.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text[-1] in ("Z", "z"):
        text = text[:-1] + "+00:00"
    else:
        text = _OFFSET_NO_COLON.sub(r"\1:\2", text)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_local(value, *, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Render an ISO-8601 UTC timestamp in the machine's local timezone.

    Converts with .astimezone() (no argument == system local zone) and formats.
    None yields ''; anything unparseable (a non-timestamp field a list column was
    pointed at) is passed through as str(value) so the field is never mangled.
    """
    if value is None:
        return ""
    dt = parse_iso_utc(value)
    if dt is None:
        return str(value)
    return dt.astimezone().strftime(fmt)


def to_utc_iso(local_date: str, *, end: bool = False) -> str:
    """Map a typed local YYYY-MM-DD into an ISO-8601 UTC instant string.

    The date is read as local midnight (the start of that day in the machine's
    zone), made aware, converted to UTC, and rendered 'YYYY-MM-DDTHH:MM:SSZ'. With
    end=True it is the start of the *next* local day, an exclusive upper bound, so a
    half-open [from, to) query covers every instant on the to-date regardless of
    zone. A value already carrying a time (length != 10) is returned unchanged, so a
    caller may pass a full timestamp verbatim.
    """
    if not isinstance(local_date, str) or len(local_date) != 10:
        return local_date
    dt = datetime.strptime(local_date, "%Y-%m-%d")
    if end:
        dt = dt + timedelta(days=1)
    aware_local = dt.astimezone()  # naive -> aware in the system local zone
    return aware_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
