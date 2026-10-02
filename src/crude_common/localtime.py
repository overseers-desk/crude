"""ISO-8601-UTC to local-time conversion for the crude site CLIs.

REST APIs return timestamps as ISO-8601 UTC; a CLI shows them in local time and
reads typed --from/--to dates in local time too. This is the dominant wire-format
conversion, so it lives here for reuse rather than in any one binary. crude_sonas
works in EJSON epoch-ms ({"$date": ms}) and keeps helpers of its own; crude_rezdy
converts its own typed bounds, an upper one to the day's last second because
Rezdy's ranges include both ends, and uses parse_iso_utc alone from here.

Local time is the zone a caller passes as `tz`, which a site CLI takes from its
config. With tz None it is the machine's zone, read from the process environment
at call time: a naive datetime's .astimezone() with no argument is interpreted in
it, and converting to it is also .astimezone() with no argument.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

# A trailing numeric offset with no colon (+0000), which datetime.fromisoformat
# rejects before Python 3.11; rewritten to +00:00.
_OFFSET_NO_COLON = re.compile(r"([+-]\d{2})(\d{2})$")


def parse_iso_utc(value, assume=timezone.utc):
    """Parse an ISO-8601 instant to an aware UTC datetime, or None if unparseable.

    A trailing 'Z' is normalised to '+00:00' and a colon is inserted into a bare
    +HHMM offset (datetime.fromisoformat rejects both before Python 3.11, and the
    project targets 3.9+). A parsed value carrying no tzinfo is read in `assume`:
    UTC by default, because the APIs document their timestamps as UTC, or the zone
    a caller passes for a time a user typed.
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
        dt = dt.replace(tzinfo=assume)
    return dt.astimezone(timezone.utc)


def format_local(value, *, fmt: str = "%Y-%m-%d %H:%M", tz=None) -> str:
    """Render an ISO-8601 UTC timestamp in `tz`, or the machine's zone when tz is None.

    None yields ''; anything unparseable (a non-timestamp field a list column was
    pointed at) is passed through as str(value) so the field is never mangled.
    """
    if value is None:
        return ""
    dt = parse_iso_utc(value)
    if dt is None:
        return str(value)
    return dt.astimezone(tz).strftime(fmt)


def to_utc_iso(local_date: str, *, end: bool = False, tz=None) -> str:
    """Map a typed local YYYY-MM-DD into an ISO-8601 UTC instant string.

    The date is read as local midnight (the start of that day in `tz`, or in the
    machine's zone when tz is None), converted to UTC, and rendered
    'YYYY-MM-DDTHH:MM:SSZ'. With end=True it is the start of the *next* local day,
    an exclusive upper bound, so a half-open [from, to) query covers every instant
    on the to-date regardless of zone. A value already carrying a time (length !=
    10) is returned unchanged, so a caller may pass a full timestamp verbatim.
    """
    if not isinstance(local_date, str) or len(local_date) != 10:
        return local_date
    dt = datetime.strptime(local_date, "%Y-%m-%d")
    if end:
        dt = dt + timedelta(days=1)
    aware_local = dt.astimezone() if tz is None else dt.replace(tzinfo=tz)
    return aware_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
