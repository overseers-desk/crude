"""LDIF (inetOrgPerson) rendering for the crude site CLIs.

Every site's people-shaped records can be exported as LDIF so that several
sites' exports concatenate into one importable file. The contract:

- stdout carries LDIF entries and blank separator lines only, nothing else, so
  the output of several runs can be concatenated safely; every diagnostic goes
  to stderr.
- each entry is objectClass inetOrgPerson plus extensibleObject, the latter so
  the createdDateTime/modifiedDateTime attributes are legal.
- timestamps are normalised to one caller-chosen timezone and rendered as
  ISO-8601 with the numeric offset, so entries from sites reporting in
  different native forms line up.

A site describes its record shape with a PersonMap; emit_ldif does the rest.
LdifSink bundles the per-invocation choices (map, site tag, timezone, base DN)
so output.emit_list/emit_record can take the whole thing as one keyword.
"""

from __future__ import annotations

import base64
import sys
from datetime import datetime, timezone
from typing import Callable, NamedTuple, Optional, Union

from crude_common.localtime import parse_iso_utc

Getter = Union[str, Callable]


class PersonMap(NamedTuple):
    """How one site's person records map onto inetOrgPerson attributes.

    ``attrs`` is an ordered mapping of LDAP attribute name (any subset of cn,
    sn, givenName, mail, telephoneNumber, mobile, o, title) to either a record
    key or a callable taking the record. ``id_key`` names the native id the
    same way. ``created``/``modified`` fetch the raw native timestamps and
    ``parse_dt`` turns one raw value into an aware datetime (or None).
    ``include`` filters records; a record it rejects is skipped with a note on
    stderr (for example company beneficiaries in a people list).
    """

    attrs: "dict[str, Getter]"
    id_key: Getter
    created: Optional[Getter] = None
    modified: Optional[Getter] = None
    parse_dt: Optional[Callable] = None
    include: Optional[Callable] = None


class LdifSink(NamedTuple):
    """One invocation's LDIF settings, passed to emit_list/emit_record."""

    pm: PersonMap
    site: str
    tz: object
    base_dn: str


def parse_epoch_ms(value):
    """Parse a millisecond epoch (int, float or digit string) to aware UTC."""
    try:
        ms = float(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def parse_naive_utc(value):
    """Parse Odoo's naive "YYYY-MM-DD HH:MM:SS" form, taking it as UTC."""
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc)


def _get(record, getter: Getter):
    if callable(getter):
        return getter(record)
    return record.get(getter)


def _needs_base64(value: str) -> bool:
    """RFC 2849 SAFE-STRING test: base64 when the value cannot go verbatim."""
    if value == "":
        return False
    if value[0] in (" ", ":", "<"):
        return True
    if value[-1] == " ":
        return True
    for ch in value:
        code = ord(ch)
        if code == 0 or code == 10 or code == 13 or code > 127:
            return True
    return False


def _fold(line: str) -> str:
    """Fold a line at 76 bytes with single-space continuations (RFC 2849)."""
    raw = line.encode("utf-8")
    if len(raw) <= 76:
        return line
    pieces = [raw[:76]]
    rest = raw[76:]
    while rest:
        pieces.append(b" " + rest[:75])
        rest = rest[75:]
    return "\n".join(p.decode("utf-8") for p in pieces)


def _attr_line(name: str, value: str) -> str:
    if _needs_base64(value):
        encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
        return _fold(f"{name}:: {encoded}")
    return _fold(f"{name}: {value}")


def _render_dt(pm: PersonMap, record, getter: Getter, tz) -> Optional[str]:
    raw = _get(record, getter)
    if raw is None or raw is False or raw == "":
        return None
    parse = pm.parse_dt or parse_iso_utc
    dt = parse(raw)
    if dt is None:
        return None
    return dt.astimezone(tz).isoformat()


def emit_ldif(items, pm: PersonMap, site: str, tz, base_dn: str) -> None:
    """Write the records as LDIF entries on stdout, each followed by a blank line.

    Attributes whose value is None, False or empty are omitted. cn and sn are
    mandatory for inetOrgPerson: cn falls back to givenName plus sn, sn falls
    back to the whole cn, and a record with no name at all is skipped with a
    warning on stderr.
    """
    for record in items:
        if pm.include is not None and not pm.include(record):
            print(f"crude: skipping non-person record ({site})", file=sys.stderr)
            continue
        native_id = _get(record, pm.id_key)
        values = {}
        for attr, getter in pm.attrs.items():
            val = _get(record, getter)
            if val is None or val is False or val == "":
                continue
            values[attr] = str(val)

        cn = values.get("cn")
        sn = values.get("sn")
        given = values.get("givenName")
        if not cn:
            cn = " ".join(p for p in (given, sn) if p) or None
        if not sn:
            sn = cn
        if not cn or not sn:
            print(
                f"crude: skipping {site} record {native_id}: no name for cn/sn",
                file=sys.stderr,
            )
            continue
        values["cn"] = cn
        values["sn"] = sn

        uid = f"{site}-{native_id}"
        lines = [
            _attr_line("dn", f"uid={uid},{base_dn}"),
            "objectClass: inetOrgPerson",
            "objectClass: extensibleObject",
            _attr_line("uid", uid),
        ]
        # Preserve the map's declared attribute order, cn/sn included even
        # when derived; iterate the declared order first, then any derived
        # name attribute the map did not declare.
        order = list(pm.attrs.keys())
        for name in ("cn", "sn"):
            if name not in order:
                order.append(name)
        for name in order:
            if name in values:
                lines.append(_attr_line(name, values[name]))
        for attr, getter in (("createdDateTime", pm.created),
                             ("modifiedDateTime", pm.modified)):
            if getter is None:
                continue
            rendered = _render_dt(pm, record, getter, tz)
            if rendered:
                lines.append(_attr_line(attr, rendered))

        # A blank line after every entry, the last included, keeps plain
        # concatenation of several crude runs a valid single LDIF stream.
        sys.stdout.write("\n".join(lines) + "\n\n")
