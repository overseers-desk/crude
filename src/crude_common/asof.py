"""WORLD_AS_OF: the office-wide as-of bound, honoured by every crude backend.

``WORLD_AS_OF`` is an environment variable holding an ISO-8601 instant with a
timezone (e.g. ``2026-07-12T17:07:00+10:00``). Three semantics, exactly:

1. **Unset** — unbounded; zero behavioural change for existing callers.
2. **Set** — nothing created after that instant may leave a tool. Queries are
   bounded at the server where the backend allows it and post-filtered where it
   does not; a record whose modified-time is after the cutoff is served in its
   current state and flagged; inherently now-valued reads refuse; **every write
   verb refuses**, because a bounded run reads the past and a write would mutate
   the live present.
3. **Set but unparseable** (a timezone-naive value counts as unparseable) —
   hard failure with a clear message, never a silent fallback: a cutoff a tool
   quietly ignores yields a contaminated run that looks valid.

This module is the shared machinery: the parse (with the hard-failure gate the
CLI root callbacks call), the server-side clamps, the client-side post-filter
and its ``_world_as_of`` JSON marker, the stderr notices, and the write
refusals. The per-backend boundary — which field is creation time, which server
parameter takes the clamp, what refuses — lives in each ``crude_<site>``
package and is documented in ``WORLD_AS_OF.design.md`` and ``docs/manual.md``.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Callable, Optional, Union

import typer

ENV = "WORLD_AS_OF"

# The JSON marker added to a record served in a state the cutoff cannot pin.
MARKER_KEY = "_world_as_of"
MUTATED = "mutated-after-cutoff"
CURRENT_STATE = "current-state"

# A trailing numeric offset with no colon (+0000), rewritten to +00:00 so
# datetime.fromisoformat accepts it before Python 3.11.
_OFFSET_NO_COLON = re.compile(r"([+-]\d{2})(\d{2})$")

# The .NET JSON date Xero emits: /Date(1672531200000+0000)/ (ms UTC epoch; the
# trailing offset is presentation only).
_DOTNET_DATE = re.compile(r"^/Date\((-?\d+)(?:[+-]\d{4})?\)/$")


class WorldAsOfError(RuntimeError):
    """A WORLD_AS_OF violation: an unusable bound value, or a refused write."""


# ----------------------------------------------------------------------
# The bound itself
# ----------------------------------------------------------------------


def _parse_iso_aware(text: str) -> datetime:
    """Parse an ISO-8601 instant that MUST carry a timezone, else raise."""
    normalised = text.strip()
    if normalised[-1:] in ("Z", "z"):
        normalised = normalised[:-1] + "+00:00"
    else:
        normalised = _OFFSET_NO_COLON.sub(r"\1:\2", normalised)
    try:
        dt = datetime.fromisoformat(normalised)
    except ValueError:
        raise WorldAsOfError(
            f"{ENV} is set to {text!r}, which is not an ISO-8601 instant "
            f"(expected e.g. 2026-07-12T17:07:00+10:00). Refusing to run: a "
            f"silently ignored cutoff would contaminate the run."
        )
    if dt.tzinfo is None:
        raise WorldAsOfError(
            f"{ENV} is set to {text!r}, which carries no timezone offset. A "
            f"naive cutoff is ambiguous; add an offset (e.g. +10:00 or Z). "
            f"Refusing to run rather than guess."
        )
    return dt


def world_as_of() -> Optional[datetime]:
    """The bound as an aware datetime, or None when unset.

    Raises WorldAsOfError when the variable is set but unparseable or
    timezone-naive — semantic 3, the hard failure.
    """
    raw = os.environ.get(ENV)
    if raw is None or raw.strip() == "":
        return None
    return _parse_iso_aware(raw)


def raw_value() -> str:
    """The variable's verbatim value, for messages (empty when unset)."""
    return os.environ.get(ENV, "")


def active() -> bool:
    """True when a (valid) bound is set."""
    return world_as_of() is not None


def check_env() -> Optional[datetime]:
    """The CLI gate: parse the bound or abort the process with a clear error.

    Called from every site CLI's root callback (and the launcher's) before any
    request, so semantic 3 fails the whole command with a nonzero exit rather
    than part-running.
    """
    try:
        return world_as_of()
    except WorldAsOfError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(2)


def bound_ms() -> Optional[int]:
    """The bound as UTC epoch milliseconds, or None."""
    b = world_as_of()
    return None if b is None else int(b.timestamp() * 1000)


def bound_s() -> Optional[int]:
    """The bound as UTC epoch seconds, or None."""
    b = world_as_of()
    return None if b is None else int(b.timestamp())


def bound_utc_iso(fmt: str = "%Y-%m-%dT%H:%M:%SZ") -> Optional[str]:
    """The bound rendered as a UTC instant string, or None."""
    b = world_as_of()
    return None if b is None else b.astimezone(timezone.utc).strftime(fmt)


# ----------------------------------------------------------------------
# Timestamp parsing for record post-filters
# ----------------------------------------------------------------------


def parse_stamp(value) -> Optional[datetime]:
    """Best-effort parse of a backend record timestamp to an aware UTC datetime.

    Covers the wire formats the crude backends actually emit: ISO-8601 strings
    (with Z, an offset, or naive-meaning-UTC — Odoo, Xero where params, Rezdy,
    Airwallex, Deputy, ATDW, Facebook), Meteor EJSON ``{"$date": ms}`` (Sonas),
    bare epoch numbers (Clover ms), and the .NET ``/Date(ms)/`` shape (Xero
    JSON bodies). Anything unrecognisable yields None — never a guess.
    """
    if value is None:
        return None
    if isinstance(value, dict):
        return parse_stamp(value.get("$date"))
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        # > 1e11 is epoch ms (1e11 s is the year 5138); else epoch seconds.
        seconds = value / 1000.0 if abs(value) > 1e11 else float(value)
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    m = _DOTNET_DATE.match(text)
    if m:
        return parse_stamp(int(m.group(1)))
    if text[-1:] in ("Z", "z"):
        text = text[:-1] + "+00:00"
    else:
        text = _OFFSET_NO_COLON.sub(r"\1:\2", text)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        # The backends whose stamps arrive naive (Odoo, Xero) document them UTC.
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _pick(record: dict, key: Union[str, Callable, None]):
    """A field out of a record: a callable, a literal key, or a dotted path."""
    if key is None or not isinstance(record, dict):
        return None
    if callable(key):
        return key(record)
    if key in record:
        return record[key]
    current = record
    for part in key.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


# ----------------------------------------------------------------------
# Server-side clamps and window checks
# ----------------------------------------------------------------------


def clamp_upper_iso(value: Optional[str], fmt: str = "%Y-%m-%dT%H:%M:%SZ") -> Optional[str]:
    """min(user upper bound, WORLD_AS_OF) as an instant string for a server param.

    Unset bound passes the value through untouched (semantic 1). With a bound,
    a missing or unparseable or later user value becomes the bound itself.
    """
    b = world_as_of()
    if b is None:
        return value
    if value is not None:
        v = parse_stamp(value)
        if v is not None and v <= b:
            return value
    return b.astimezone(timezone.utc).strftime(fmt)


def clamp_upper_ms(value: Optional[int]) -> Optional[int]:
    """min(user upper bound, WORLD_AS_OF) in UTC epoch milliseconds."""
    b_ms = bound_ms()
    if b_ms is None:
        return value
    if value is None:
        return b_ms
    return min(int(value), b_ms)


def check_window_start(value, what: str = "--from") -> None:
    """Refuse when a query window starts after the cutoff.

    An empty-looking result for a window the bound can never populate is a lie
    of omission; the design calls for an explicit refusal instead.
    """
    b = world_as_of()
    if b is None or value is None:
        return
    v = parse_stamp(value)
    if v is not None and v > b:
        refuse(f"the {what} window starts after the cutoff; no bounded data can exist there")


# ----------------------------------------------------------------------
# Client-side post-filter and flagging
# ----------------------------------------------------------------------


def post_filter(records, created=None, modified=None, parse=parse_stamp):
    """The honest rule over a record list, under the bound.

    Drops records whose creation stamp exceeds the bound; marks records whose
    modified stamp exceeds it with ``_world_as_of: "mutated-after-cutoff"``
    (a copy — inputs are not mutated). ``created``/``modified`` are a field
    name, a dotted path, or a callable over the record. A record with no
    readable creation stamp is kept: absence of evidence is not a future date.

    Returns ``(kept, dropped_count, mutated_count)``. With the bound unset the
    input is returned unchanged with zero counts.
    """
    b = world_as_of()
    if b is None:
        return records, 0, 0
    kept, dropped, mutated = [], 0, 0
    for rec in records:
        c = parse(_pick(rec, created)) if created is not None else None
        if c is not None and c > b:
            dropped += 1
            continue
        m = parse(_pick(rec, modified)) if modified is not None else None
        if m is not None and m > b and isinstance(rec, dict):
            rec = dict(rec)
            rec[MARKER_KEY] = MUTATED
            mutated += 1
        kept.append(rec)
    return kept, dropped, mutated


def bound_records(records, created=None, modified=None, parse=parse_stamp,
                  what: str = "record"):
    """post_filter plus the one-line stderr notice; returns the kept records."""
    kept, dropped, mutated = post_filter(records, created, modified, parse)
    emit_notice(what, dropped, mutated)
    return kept


def check_record(record, created=None, modified=None, parse=parse_stamp,
                 what: str = "record"):
    """The single-record (get) form of the honest rule.

    Refuses (exit 1) when the record was created after the cutoff — it did not
    exist in the bounded world. Returns the record flagged when its modified
    stamp is after the cutoff, else unchanged. Bound unset: unchanged.
    """
    b = world_as_of()
    if b is None or not isinstance(record, dict):
        return record
    c = parse(_pick(record, created)) if created is not None else None
    if c is not None and c > b:
        refuse(f"this {what} was created after the cutoff")
    m = parse(_pick(record, modified)) if modified is not None else None
    if m is not None and m > b:
        record = dict(record)
        record[MARKER_KEY] = MUTATED
        emit_notice(what, 0, 1)
    return record


def flag_current_state(record):
    """A copy of a record marked as current-state (no usable audit stamp)."""
    if world_as_of() is None or not isinstance(record, dict):
        return record
    out = dict(record)
    out.setdefault(MARKER_KEY, CURRENT_STATE)
    return out


# ----------------------------------------------------------------------
# Messaging and refusals
# ----------------------------------------------------------------------


def emit_notice(what: str, dropped: int, mutated: int) -> None:
    """The one stderr line per bounded read command (silent when unbounded)."""
    if world_as_of() is None:
        return
    typer.echo(
        f"WORLD_AS_OF {raw_value()}: {dropped} {what}(s) created after cutoff "
        f"dropped; {mutated} mutated after cutoff served as current state",
        err=True,
    )


def emit_current_state(what: str) -> None:
    """The stderr disclosure for a surface this backend keeps no history for."""
    if world_as_of() is None:
        return
    typer.echo(
        f"WORLD_AS_OF {raw_value()}: {what} reflect(s) current state, not the "
        f"state as of the cutoff (this backend keeps no history for it)",
        err=True,
    )


def refuse(reason: str):
    """Refuse a read the bound makes unanswerable: clear message, exit 1."""
    typer.echo(f"Error: WORLD_AS_OF {raw_value()}: {reason}.", err=True)
    raise typer.Exit(1)


def _write_refusal(what: str) -> str:
    return (
        f"WORLD_AS_OF is set ({raw_value()}); refusing to {what}: a bounded "
        f"run reads the past, and a write would mutate the live present"
    )


def refuse_write_cli(what: str) -> None:
    """The CLI-layer write gate (crude_common.writeio.do_write): message + exit 1.

    A no-op when the bound is unset.
    """
    if world_as_of() is None:
        return
    typer.echo(f"Error: {_write_refusal(what)}.", err=True)
    raise typer.Exit(1)


def guard_write(what: str) -> None:
    """The client-layer write gate: raise WorldAsOfError when the bound is set.

    Placed on every write path that does not pass through ``do_write`` (Sonas
    DDP method calls, Deputy resource writes, ATDW listing writes) and, belt
    and braces, on the mutating transport verbs of the backends whose writes
    do (Xero, Facebook, Rezdy, Airwallex, Clover), so a library caller is
    refused exactly like a CLI one. A no-op when the bound is unset.
    """
    if world_as_of() is not None:
        raise WorldAsOfError(_write_refusal(what))
