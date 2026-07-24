"""Typer CLI for Sonas wedding-venue software: crude-sonas.

Sonas has no public API; this drives its Meteor DDP backend directly (see
``crude_sonas.client`` and ``docs/sonas.md``). This minimal surface ships the
``event`` reads; the full resource map and the plan for the rest live in
``docs/sonas.md``.
"""

import json
import sys
from pathlib import Path
from typing import List, Optional, Tuple

import typer
from rich.console import Console
from rich.table import Table

from crude_common import asof
from crude_common.claude_command import register_claude_command
from crude_common.config import (
    account,
    find_config,
    read_config,
    resolve_account,
    resolve_base_dn,
    resolve_timezone,
    s,
)
from crude_common.ldif import LdifSink, PersonMap, parse_epoch_ms
from crude_common.output import emit_list
from crude_sonas.client import (
    EPOCH_1900_MS,
    EPOCH_2100_MS,
    EVENT_STATUS,
    EVENT_TYPE,
    date_str,
    to_ejson_date,
    to_ejson_date_end,
)

app = typer.Typer(help="crude-sonas — Sonas wedding-venue software (app.sonas.events).")
event_app = typer.Typer(help="Events (weddings and other bookings).")
app.add_typer(event_app, name="event")
guest_app = typer.Typer(help="An event's named guests and its headcount.")
app.add_typer(guest_app, name="guest")
timeline_app = typer.Typer(help="An event's wedding-day timeline entries.")
app.add_typer(timeline_app, name="timeline")
note_app = typer.Typer(help="Staff notes on an event.")
app.add_typer(note_app, name="note")
transaction_app = typer.Typer(help="An event's transactions: charges, payments, refunds, discounts.")
app.add_typer(transaction_app, name="transaction")
invoice_app = typer.Typer(help="An event's financial records: proformas, invoices, credit notes.")
app.add_typer(invoice_app, name="invoice")
service_booking_app = typer.Typer(help="An event's bookings of catalog services.")
app.add_typer(service_booking_app, name="service-booking")
message_app = typer.Typer(help="An event's messages (email and internal).")
app.add_typer(message_app, name="message")
document_app = typer.Typer(help="An event's document files.")
app.add_typer(document_app, name="document")
terms_app = typer.Typer(help="An event's terms-and-conditions records.")
app.add_typer(terms_app, name="terms")
activity_app = typer.Typer(help="An event's activity log (system and staff entries).")
app.add_typer(activity_app, name="activity")
availability_app = typer.Typer(help="Venue appointment-availability windows (bookable slot definitions).")
app.add_typer(availability_app, name="availability")
appointment_app = typer.Typer(help="Calendar appointments: show-arounds, meetings, holidays, internal entries.")
app.add_typer(appointment_app, name="appointment")
tasting_app = typer.Typer(help="Tasting events and their bookings.")
app.add_typer(tasting_app, name="tasting")
console = Console()

register_claude_command(app)


def _make_client(config: dict):
    from crude_sonas.client import SonasClient, DEFAULT_FINGERPRINT
    sonas = resolve_account(config, "sonas", account())
    user = sonas.get("username")
    digest = sonas.get("password_hash")
    if not (user and digest):
        typer.echo(
            "Error: config.toml must contain [sonas] username and password_hash "
            "(the SHA-256 of the password). See docs/sonas.md.",
            err=True,
        )
        raise typer.Exit(1)
    fingerprint = sonas.get("fingerprint") or DEFAULT_FINGERPRINT
    return SonasClient(user, digest, fingerprint, tenant=sonas.get("tenant"))


def _client():
    """Construct the client from the discovered config (the per-command path)."""
    return _make_client(read_config(find_config()))


# ----------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------


def _couple(doc: dict) -> str:
    customers = doc.get("customers") or []
    mains = [c for c in customers if c.get("main")] or customers
    names = [
        " ".join(p for p in (c.get("firstname"), c.get("lastname")) if p).strip()
        for c in mains
    ]
    return " & ".join(n for n in names if n)


def _guests(doc: dict) -> str:
    counts = doc.get("currentMain") or {}
    total = sum(v for v in counts.values() if isinstance(v, (int, float)))
    return str(int(total)) if total else ""


def _render_events(events: list) -> None:
    table = Table(show_header=True, header_style="bold magenta")
    for col in ("Id", "Ref", "Date", "Status", "Type", "Couple", "Guests", "Name"):
        table.add_column(col, style="dim" if col in ("Id", "Ref") else None)
    for ev in events:
        table.add_row(
            s(ev.get("_id")),
            s(ev.get("reference")),
            date_str(ev.get("date")),
            EVENT_STATUS.get(ev.get("status"), s(ev.get("status"))),
            EVENT_TYPE.get(ev.get("type"), s(ev.get("type"))),
            _couple(ev),
            _guests(ev),
            s(ev.get("name")),
        )
    console.print(table)


def _cell(value, max_len: int = 80) -> str:
    """Render one value for a table cell: EJSON dates as dates, containers
    summarised, scalars as text, truncated to ``max_len``."""
    if isinstance(value, dict) and "$date" in value:
        cell = date_str(value)
    elif isinstance(value, dict):
        cell = "(object)"
    elif isinstance(value, list):
        cell = f"{len(value)} item(s)"
    else:
        cell = s(value)
    if len(cell) > max_len:
        cell = cell[: max_len - 3] + "..."
    return cell


def _render_record(item: dict) -> None:
    # Local, not crude_common.output.render_record: sonas values are Meteor EJSON,
    # so cells go through _cell to render {"$date": ...} as a date.
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Field")
    table.add_column("Value")
    for key, value in item.items():
        table.add_row(key, _cell(value, 200))
    console.print(table)


def _dig(item: dict, key: str):
    """``item[key]``, or a dotted-path walk (dict keys and list indices) when
    the literal key is absent, e.g. ``contactData.email``, ``emails.0.address``."""
    if key in item:
        return item[key]
    cur = item
    for part in key.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def _emit(items: list, output_json: bool,
          columns: Optional[List[Tuple[str, str]]] = None, what: str = "row") -> None:
    """Render a list result: raw JSON, or a Rich table plus a count line.

    ``columns`` is (header, key) pairs, the key a ``_dig`` path; None derives
    headers from the union of the items' keys (minus ``_collection``), capped
    at 8 to stay readable.
    """
    if output_json:
        typer.echo(json.dumps(items, indent=2))
        return
    if items:
        if columns is None:
            keys: List[str] = []
            for item in items:
                for key in item:
                    if key != "_collection" and key not in keys:
                        keys.append(key)
            columns = [(k, k) for k in keys[:8]]
        table = Table(show_header=True, header_style="bold magenta")
        for header, _key in columns:
            table.add_column(header)
        for item in items:
            table.add_row(*(_cell(_dig(item, key)) for _header, key in columns))
        console.print(table)
    typer.echo(f"\n{len(items)} {what}(s).")


def _emit_record(item, output_json: bool) -> None:
    if output_json:
        typer.echo(json.dumps(item, indent=2))
        return
    _render_record(item)


def _status_matches(doc: dict, query: str) -> bool:
    status = doc.get("status")
    return str(status) == query or EVENT_STATUS.get(status, "").lower() == query.lower()


# Enquiry-group statuses (docs/sonas.md §7); leaving the group is the
# contract-relevant move, so it asks for confirmation.
ENQUIRY_GROUP = {0, 3, 4, 7}


def _parse_enum(value: str, names: dict, what: str) -> int:
    """Resolve an enum name or number against its table."""
    if value.lstrip("-").isdigit() and int(value) in names:
        return int(value)
    for num, name in names.items():
        if name.lower() == value.lower():
            return num
    choices = ", ".join(f"{k} {v}" for k, v in names.items())
    typer.echo(f"Error: unknown {what} {value!r}; one of: {choices}.", err=True)
    raise typer.Exit(2)


def _parse_status(value: str) -> int:
    return _parse_enum(value, EVENT_STATUS, "status")


def _range_params(from_: Optional[str], to: Optional[str]) -> list:
    """EJSON [from, to] params for the *ByDateRange pubs; default all time,
    the same wide range `event list` uses."""
    return [to_ejson_date(from_) if from_ else {"$date": EPOCH_1900_MS},
            to_ejson_date_end(to) if to else {"$date": EPOCH_2100_MS}]


def _dt_str(value) -> str:
    """Render an EJSON datetime as YYYY-MM-DD HH:MM in local time; pass others through."""
    if isinstance(value, dict) and "$date" in value:
        from datetime import datetime
        return datetime.fromtimestamp(value["$date"] / 1000).strftime("%Y-%m-%d %H:%M")
    return s(value)


# ----------------------------------------------------------------------
# Command plumbing shared by the resource sub-apps
# ----------------------------------------------------------------------


def _read_data(data: Optional[str], file: Optional[str]) -> dict:
    """Resolve write input: --data inline JSON, then -f file, then stdin."""
    if data is not None:
        raw = data
    elif file is not None:
        with open(file, "r") as f:
            raw = f.read()
    elif not sys.stdin.isatty():
        raw = sys.stdin.read()
    else:
        typer.echo(
            "Error: provide JSON via --data, -f/--file, or stdin.", err=True
        )
        raise typer.Exit(1)
    try:
        parsed = json.loads(raw)
    except ValueError as e:
        typer.echo(f"Error: invalid JSON: {e}", err=True)
        raise typer.Exit(1)
    if not isinstance(parsed, dict):
        typer.echo("Error: JSON body must be an object.", err=True)
        raise typer.Exit(1)
    return parsed


def _require_event(client, event_id: str) -> None:
    """Error out if no event has this id, instead of reporting an empty read.

    Sonas publications accept any id, send `ready`, and publish nothing for an
    id that matches nothing, so an unmatched id otherwise reads as "0 records"
    on the per-event lists. The eventBasicInfo events doc is the existence test
    `document_list` already relies on. Call this before the read, never inside a
    `try` that catches Exception, since `typer.Exit` is an Exception subclass."""
    try:
        basic = client.read_pub("eventBasicInfo", [event_id])
    except Exception as e:
        typer.echo(f"Error checking event {event_id}: {e}", err=True)
        raise typer.Exit(1)
    if not any(d.get("_collection") == "events" for d in basic):
        typer.echo(f"Error: event {event_id} not found", err=True)
        raise typer.Exit(1)


def _pub_list(pub: str, params: list, columns: Optional[List[Tuple[str, str]]],
              output_json: bool, what: str, collection: Optional[str] = None,
              event_id: Optional[str] = None, created=None,
              current: bool = False) -> None:
    """List command body: subscribe to a publication and emit its documents.

    Pass ``event_id`` for an event-scoped pub to validate the event first.
    Under WORLD_AS_OF, ``created`` names the creation stamp to post-filter on;
    ``current`` serves the docs current-state-flagged instead (no stamp).
    """
    client = _client()
    if event_id is not None:
        _require_event(client, event_id)
    try:
        items = client.read_pub(pub, params, collection=collection)
    except Exception as e:
        typer.echo(f"Error fetching {what}s: {e}", err=True)
        raise typer.Exit(1)
    finally:
        client.close()
    if created is not None:
        items = _asof_filter(items, what, created)
    elif current:
        items = asof.current_state(items, f"{what}s")
    _emit(items, output_json, columns=columns, what=what)


def _do_call(method: str, arg: dict, what: str, confirm: Optional[str] = None,
             yes: bool = False, output_json: bool = False) -> None:
    """Write command body: confirm if asked, invoke a DDP method, report."""
    if confirm and not yes:
        typer.confirm(confirm, abort=True)
    client = _client()
    try:
        result = client.call(method, arg)
    except Exception as e:
        typer.echo(f"Error: {what}: {e}", err=True)
        raise typer.Exit(1)
    finally:
        client.close()
    if output_json:
        typer.echo(json.dumps(result if result is not None else {"ok": True}, indent=2))
        return
    typer.echo(f"{what}: done.")
    if isinstance(result, dict):
        typer.echo(json.dumps(result))
    elif result is not None and not isinstance(result, list):
        typer.echo(str(result))


def _asof_filter(items: list, what: str, created="createdAt") -> list:
    """The Sonas knowledge bound over a per-event collection.

    All bounding is client-side by construction of DDP. Docs created after the
    cutoff are dropped by ``created`` (a key or a callable); a doc with no
    readable creation stamp is kept but flagged current-state, since Sonas
    documents are mutated in place and absence of a stamp is not a date.
    """
    if not asof.active():
        return items
    getter = created if callable(created) else (
        lambda d: d.get(created) if isinstance(d, dict) else None)
    kept, dropped, _ = asof.post_filter(items, getter)
    kept = [d if asof.parse_stamp(getter(d)) is not None else asof.flag_current_state(d)
            for d in kept]
    asof.emit_notice(what, dropped, 0)
    return kept


def _event_created(doc: dict):
    """An event's knowledge-time creation: the enquiry's own date where the doc
    carries it, else the doc's createdAt. The event date is the wedding's
    domain date and is never consulted: a wedding next year enquired-about last
    year is legitimately visible under a cutoff between the two."""
    enquiry = doc.get("enquiryData")
    if isinstance(enquiry, dict) and enquiry.get("date") is not None:
        return enquiry.get("date")
    return doc.get("createdAt")


def _matches(value, term: str) -> bool:
    """Case-insensitive substring match against any string in the document."""
    if isinstance(value, dict):
        return any(_matches(v, term) for v in value.values())
    if isinstance(value, list):
        return any(_matches(v, term) for v in value)
    return isinstance(value, str) and term.lower() in value.lower()


def _tabular_list(table: str, data_pub: str, columns: Optional[List[Tuple[str, str]]],
                  output_json: bool, what: str, limit: int = 50, search: str = "",
                  selector: Optional[dict] = None, collection: Optional[str] = None) -> None:
    """List command body for aldeed:tabular tables (the two-step in docs/sonas.md §5).

    Pass ``collection`` when it is known: the auto-detect mode misses documents
    already in the store (e.g. the logged-in user's doc for UserList). ``search``
    filters client-side: the catalog tables all declare ``searching: false`` and
    ignore the wire searchTerm (docs/sonas.md §6.4), so the rows (fetched wide)
    are matched here instead.
    """
    client = _client()
    try:
        rows, info = client.read_tabular(
            table, data_pub=data_pub, selector=selector,
            limit=max(limit, 500) if search else limit, search=search,
            collection=collection,
        )
    except Exception as e:
        typer.echo(f"Error fetching {what}s: {e}", err=True)
        raise typer.Exit(1)
    finally:
        client.close()
    if info.get("recordsTotal") and not rows:
        typer.echo(
            f"Warning: {table} counts {info['recordsTotal']} record(s) but data pub "
            f"{data_pub!r} delivered none (signature mismatch? see docs/sonas.md §5).",
            err=True,
        )
    if search:
        rows = [r for r in rows if _matches(r, search)][:limit]
    rows = asof.current_state(rows, f"{what}s (mutable catalog)")
    _emit(rows, output_json, columns=columns, what=what)
    if output_json:
        return
    if search:
        typer.echo(f"{len(rows)} of {info.get('recordsTotal')} record(s) match {search!r}.")
    elif info.get("recordsFiltered") is not None and info.get("recordsTotal") is not None:
        typer.echo(f"{info['recordsFiltered']} of {info['recordsTotal']} record(s).")


# ----------------------------------------------------------------------
# event
# ----------------------------------------------------------------------


@event_app.command("list")
def event_list(
    from_: Optional[str] = typer.Option(None, "--from", help="On or after this date (YYYY-MM-DD)."),
    to: Optional[str] = typer.Option(None, "--to", help="On or before this date (YYYY-MM-DD)."),
    status: Optional[str] = typer.Option(None, "--status", help="Filter by status name or number."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """List events (weddings and other bookings)."""
    client = _make_client(read_config(find_config()))
    try:
        events = client.list_events(from_, to)
    except Exception as e:
        typer.echo(f"Error listing events: {e}", err=True)
        raise typer.Exit(1)
    finally:
        client.close()
    if status:
        events = [e for e in events if _status_matches(e, status)]
    # Knowledge bound: drop events whose enquiry entered the world after the
    # cutoff; the survivors' docs are mutated in place on every status change,
    # so they are served as current state, flagged.
    if asof.active():
        events = _asof_filter(events, "event", _event_created)
        events = [asof.flag_current_state(e) for e in events]
    events.sort(key=lambda e: (e.get("date") or {}).get("$date", 0) if isinstance(e.get("date"), dict) else 0)
    if output_json:
        typer.echo(json.dumps(events, indent=2))
        return
    _render_events(events)
    typer.echo(f"\n{len(events)} event(s).")


@event_app.command("get")
def event_get(
    event_id: str = typer.Argument(..., help="Event document id."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Show a single event."""
    client = _make_client(read_config(find_config()))
    try:
        event = client.get_event(event_id)
    except Exception as e:
        typer.echo(f"Error fetching event {event_id}: {e}", err=True)
        raise typer.Exit(1)
    finally:
        client.close()
    if asof.active():
        created = asof.parse_stamp(_event_created(event))
        if created is not None and created > asof.world_as_of():
            asof.refuse(f"event {event_id} was created after the cutoff")
        event = asof.current_state(event, "this event document (mutated in place)")
    if output_json:
        typer.echo(json.dumps(event, indent=2))
        return
    _render_record(event)


# Pubs read per event for the corpus export. Every one is a DDP subscription
# (read), never a method call, so the export path cannot write or delete.
# eventBasicInfo multi-cursors the event doc + venue + timelines; the rest are
# the same per-event read pubs the `transaction`/`invoice`/`service-booking`/
# `message` list verbs use. Contact details (email/phone) live on the customer
# `users` docs published by eventCustomersInfo, not on the event's `customers`
# stub, so that pub is required for identity/contact.
def _bundle_event(client, event_id: str) -> dict:
    """Collect one event's full record from its read pubs into a plain dict."""
    basic = client.read_pub("eventBasicInfo", [event_id])
    event = next((d for d in basic if d.get("_collection") == "events"), None)
    venues = [d for d in basic if d.get("_collection") == "venues"]
    timelines = [d for d in basic if d.get("_collection") == "timelines"]
    sb = client.read_pub("eventServiceBookings", [event_id])
    return {
        "event_id": event_id,
        "event": event,
        "venue": venues[0] if venues else None,
        "timelines": timelines,
        "customers_info": client.read_pub("eventCustomersInfo", [event_id]),
        "enquiry_data": client.read_pub("enquiryData", [event_id]),
        "messages": client.read_pub("eventMessages", [event_id], collection="messages"),
        "transactions": client.read_pub("eventTransactions", [event_id],
                                        collection="transactions"),
        "financial_records": client.read_pub("eventFinancialRecords", [event_id],
                                             collection="financial-records"),
        "service_bookings": [d for d in sb if d.get("_collection") == "service-bookings"],
        "services": [d for d in sb if d.get("_collection") == "services"],
    }


def _asof_bundle(bundle: dict):
    """The corpus-export boundary for one bundle, per the design.

    Returns None when the whole event's enquiry entered the world after the
    cutoff (the bundle is dropped from the corpus). Otherwise the volatile
    collections are filtered on their creation stamps, the mutable docs
    (event, timelines, service bookings) are flagged current-state, and the
    bundle carries a ``_world_as_of`` summary so a consumer can discount what
    the cutoff cannot pin.
    """
    bound = asof.world_as_of()
    if bound is None:
        return bundle
    event = bundle.get("event") or {}
    created = asof.parse_stamp(_event_created(event))
    if created is None:
        # No enquiry date on the doc: fall back to the earliest creation stamp
        # anywhere in the bundle, the design's stated substitute.
        stamps = [asof.parse_stamp(d.get("createdAt"))
                  for key in ("messages", "transactions", "financial_records")
                  for d in bundle.get(key) or [] if isinstance(d, dict)]
        stamps = [s_ for s_ in stamps if s_ is not None]
        created = min(stamps) if stamps else None
    if created is not None and created > bound:
        return None
    dropped = {}
    for key in ("messages", "transactions", "financial_records"):
        docs = bundle.get(key) or []
        kept, n_dropped, _ = asof.post_filter(docs, "createdAt")
        bundle[key] = kept
        dropped[key] = n_dropped
    if isinstance(bundle.get("event"), dict):
        bundle["event"] = asof.flag_current_state(bundle["event"])
    bundle["timelines"] = [asof.flag_current_state(d) for d in bundle.get("timelines") or []]
    bundle["service_bookings"] = [asof.flag_current_state(d)
                                  for d in bundle.get("service_bookings") or []]
    bundle[asof.MARKER_KEY] = {
        "cutoff": asof.raw_value(),
        "event_doc": asof.CURRENT_STATE,
        "dropped": dropped,
        "enquiry_created": created.isoformat() if created else None,
    }
    return bundle


def _index_row(bundle: dict) -> dict:
    """One manifest row: identity and per-event counts, no nested arrays."""
    event = bundle.get("event") or {}
    main = next((c for c in (event.get("customers") or []) if c.get("main")),
                (event.get("customers") or [{}])[0] if event.get("customers") else {})
    by_id = {d.get("_id"): d for d in bundle.get("customers_info") or []}
    main_user = by_id.get(main.get("userId"), {})
    return {
        "event_id": bundle["event_id"],
        "status": EVENT_STATUS.get(event.get("status"), s(event.get("status"))),
        "type": EVENT_TYPE.get(event.get("type"), s(event.get("type"))),
        "date": date_str(event.get("date")) if event.get("date") else "",
        "name": _couple(event),
        "email": main_user.get("email") or main.get("email") or "",
        "messages": len(bundle.get("messages") or []),
        "transactions": len(bundle.get("transactions") or []),
        "financial_records": len(bundle.get("financial_records") or []),
    }


@event_app.command("export")
def event_export(
    out: str = typer.Option(..., "--out", help="Directory to write the corpus into."),
    status: List[str] = typer.Option(
        None, "--status",
        help="Statuses to include (name or number); repeatable. Default: all eight."),
):
    """Export the full enquiry corpus: one JSON per event plus an index.json.

    Enumerates every event via the EventList tabular read (so date-less and
    exhausted enquiries that `event list` cannot reach are included), then writes
    `<event_id>.json` carrying the event, venue, customer identity/contact,
    messages, and financial records (transactions, invoices, service bookings).
    Read-only by construction: only subscriptions are used, never a DDP method.
    """
    statuses = [_parse_status(v) for v in status] if status else list(range(8))
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    client = _client()
    index, failures = [], []
    try:
        ids = client.iter_all_event_ids(statuses)
        typer.echo(f"Enumerated {len(ids)} event(s); exporting to {out_dir}...")
        events_dropped = 0
        for n, event_id in enumerate(ids, 1):
            try:
                bundle = _bundle_event(client, event_id)
            except Exception as e:
                failures.append((event_id, str(e)))
                typer.echo(f"  [{n}/{len(ids)}] {event_id}: FAILED — {e}", err=True)
                continue
            bundle = _asof_bundle(bundle)
            if bundle is None:
                events_dropped += 1
                continue
            (out_dir / f"{event_id}.json").write_text(
                json.dumps(bundle, indent=2, ensure_ascii=False))
            index.append(_index_row(bundle))
    except Exception as e:
        typer.echo(f"Error exporting corpus: {e}", err=True)
        raise typer.Exit(1)
    finally:
        client.close()
    if asof.active():
        # Under a bound the index carries the boundary summary alongside the
        # rows, so the corpus discloses on disk what it excludes and flags.
        payload = {"world_as_of": {"cutoff": asof.raw_value(),
                                   "events_dropped": events_dropped,
                                   "event_docs": asof.CURRENT_STATE},
                   "events": index}
        asof.emit_notice("event", events_dropped, len(index))
    else:
        payload = index
    (out_dir / "index.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    typer.echo(f"Wrote {len(index)} event file(s) + index.json to {out_dir}.")
    if failures:
        typer.echo(f"{len(failures)} event(s) failed; see stderr above.", err=True)


def _enquiry_sources(client) -> list:
    """The tenant's ``enquiry_source`` option list: category docs tagged
    ``enquiry_source`` (docs/sonas.md §6.3), read via the ``categories`` pub."""
    return [c for c in client.read_categories() if c.get("tag") == "enquiry_source"]


def _resolve_enquiry_source_id(client, source: str) -> str:
    """Resolve a ``--source`` to its category id: an exact id match, else a
    case-insensitive name match among the ``enquiry_source`` categories. Errors
    (listing the available sources) on no match or an ambiguous name."""
    sources = _enquiry_sources(client)
    for c in sources:
        if c.get("_id") == source:
            return source
    matches = [c for c in sources if (c.get("name") or "").lower() == source.lower()]
    if len(matches) == 1:
        return matches[0]["_id"]
    names = ", ".join(sorted(s(c.get("name")) for c in sources)) or "(none)"
    if not matches:
        typer.echo(f"Error: no enquiry source named {source!r}. Available: {names}.", err=True)
    else:
        typer.echo(f"Error: {source!r} matches {len(matches)} sources; use the id. "
                   f"Available: {names}.", err=True)
    raise typer.Exit(1)


@event_app.command("leads")
def event_leads(
    source: str = typer.Option(
        ..., "--source", help="Lead source: an enquiry_source name (e.g. \"Easy "
        "Weddings\", case-insensitive) or its category id."),
    from_: Optional[str] = typer.Option(
        None, "--from", help="Enquiry made on or after this date (YYYY-MM-DD). "
        "This is the enquiry date, not the wedding date."),
    to: Optional[str] = typer.Option(
        None, "--to", help="Enquiry made on or before this date (YYYY-MM-DD). "
        "This is the enquiry date, not the wedding date."),
    status: List[str] = typer.Option(
        None, "--status", help="Statuses to include (name or number); repeatable. "
        "Default: all (a converted lead still counts as one from its source)."),
    show_list: bool = typer.Option(
        False, "--list", help="Also print the matched leads, not just the count."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Count leads from a source, by enquiry date.

    A lead is an enquiry tagged with a lead source. This counts every event
    whose ``enquiryData.sourceId`` is the given source and whose enquiry date
    (``enquiryData.date``) falls in [--from, --to]. Unlike `event list`, it
    reaches the date-less fresh enquiries too (via the EventList tabular read).
    """
    client = _client()
    try:
        source_id = _resolve_enquiry_source_id(client, source)
        selector = {"enquiryData.sourceId": source_id}
        if status:
            selector["status"] = {"$in": [_parse_status(v) for v in status]}
        date_range = {}
        if from_:
            date_range["$gte"] = to_ejson_date(from_)
        if to:
            date_range["$lt"] = to_ejson_date_end(to)
        if date_range:
            selector["enquiryData.date"] = date_range
        ids, count = client.event_ids_matching(selector)
        leads = []
        if show_list or output_json:
            leads = [_lead_row(client, event_id) for event_id in ids]
    except Exception as e:
        typer.echo(f"Error counting leads: {e}", err=True)
        raise typer.Exit(1)
    finally:
        client.close()
    if output_json:
        typer.echo(json.dumps(
            {"source": source, "source_id": source_id, "from": from_, "to": to,
             "count": count, "leads": leads}, indent=2))
        return
    if show_list:
        _emit(leads, output_json=False, columns=[
            ("Id", "event_id"), ("Enquired", "enquiry_date"), ("Wedding", "wedding_date"),
            ("Status", "status"), ("Couple", "couple"),
        ], what="lead")
    else:
        typer.echo(f"{count} lead(s) from {source}"
                   + (f" enquired {from_ or '…'}–{to or '…'}" if (from_ or to) else "")
                   + ".")


def _lead_row(client, event_id: str) -> dict:
    """One lead's display row: identity and the enquiry vs wedding dates. The
    event doc (status, wedding date, couple) comes from eventBasicInfo, the
    per-event read ``_require_event`` uses; the enquiry date is taken from its
    ``enquiryData``, falling back to the ``enquiryData`` pub when that field is
    not projected."""
    basic = client.read_pub("eventBasicInfo", [event_id])
    ev = next((d for d in basic if d.get("_collection") == "events"), {})
    enq_date = (ev.get("enquiryData") or {}).get("date")
    if enq_date is None:
        for d in client.read_pub("enquiryData", [event_id]):
            cand = (d.get("enquiryData") or {}).get("date") if isinstance(d, dict) else None
            if cand is not None:
                enq_date = cand
                break
    return {
        "event_id": event_id,
        "enquiry_date": date_str(enq_date) if enq_date else "",
        "wedding_date": date_str(ev.get("date")) if ev.get("date") else "",
        "status": EVENT_STATUS.get(ev.get("status"), s(ev.get("status"))),
        "couple": _couple(ev),
    }


def _now_ejson() -> dict:
    import time as _time
    return {"$date": int(_time.time() * 1000)}


@event_app.command("create-enquiry")
def event_create_enquiry(
    venue: Optional[str] = typer.Option(None, "--venue", help="Venue id (doc.venueId)."),
    email: Optional[str] = typer.Option(None, "--email", help="Main customer email."),
    firstname: Optional[str] = typer.Option(None, "--firstname", help="Main customer first name."),
    lastname: Optional[str] = typer.Option(None, "--lastname", help="Main customer last name."),
    telephone: Optional[str] = typer.Option(None, "--telephone", help="Main customer phone."),
    type_: Optional[int] = typer.Option(None, "--type", help="EventTypeEnum number (0 Wedding)."),
    date_desired: Optional[str] = typer.Option(
        None, "--date-desired", help="Free-text desired date(s) (enquiryData.dateDesired)."
    ),
    data: Optional[str] = typer.Option(None, "--data", help="Full doc as JSON (flags overlay it)."),
    file: Optional[str] = typer.Option(None, "--file", "-f", help="Read the doc JSON from a file."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Create an enquiry (eventCreateEnquiry); prints the new event id.

    The doc is flat: venueId, type, email/firstname/lastname[/telephone] of the
    main customer, and enquiryData ("date" = when the enquiry was made, filled in
    automatically; "dateDesired" is free text). The event itself has no date until
    hold-date or change-date sets one, so a fresh enquiry does not appear in
    `event list` (which reads by date range).
    """
    doc = _read_data(data, file) if (data is not None or file is not None) else {}
    for key, value in (("venueId", venue), ("email", email), ("firstname", firstname),
                       ("lastname", lastname), ("telephone", telephone), ("type", type_)):
        if value is not None:
            doc[key] = value
    doc.setdefault("enquiryData", {}).setdefault("date", _now_ejson())
    if date_desired is not None:
        doc["enquiryData"]["dateDesired"] = date_desired
    missing = [k for k in ("venueId", "email", "firstname", "lastname") if not doc.get(k)]
    if missing:
        typer.echo(f"Error: doc is missing required key(s): {', '.join(missing)}.", err=True)
        raise typer.Exit(1)
    _do_call("eventCreateEnquiry", {"doc": doc}, "create enquiry", output_json=output_json)


@event_app.command("change-status")
def event_change_status(
    event_id: str = typer.Argument(..., help="Event document id."),
    status: str = typer.Argument(..., help="Target status, name or number (e.g. Enquiry, 3)."),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
):
    """Change an event's status (eventChangeStatus), pre-checked with eventCanChangeStatus."""
    to_status = _parse_status(status)
    if to_status not in ENQUIRY_GROUP and not yes:
        typer.confirm(
            f"Status {EVENT_STATUS[to_status]} leaves the enquiry group; continue?",
            abort=True,
        )
    client = _client()
    try:
        try:
            answer = client.call("eventCanChangeStatus", {"eventId": event_id, "toStatus": to_status})
        except Exception as e:
            typer.echo(f"Error: eventCanChangeStatus: {e}", err=True)
            raise typer.Exit(1)
        # None means no objection; any other answer is the server's verdict.
        if answer is not None and not answer:
            typer.echo(f"Error: server refuses this status change: {json.dumps(answer)}", err=True)
            raise typer.Exit(1)
        if answer is not None:
            typer.echo(f"Server notes: {json.dumps(answer)}")
        try:
            client.call("eventChangeStatus", {"eventId": event_id, "toStatus": to_status})
        except Exception as e:
            typer.echo(f"Error: change status: {e}", err=True)
            raise typer.Exit(1)
    finally:
        client.close()
    typer.echo(f"change status to {EVENT_STATUS[to_status]}: done.")


def _date_change(method: str, event_id: str, date: str, end_date: Optional[str],
                 ceremony_date: Optional[str], what: str) -> None:
    """Shared body of change-date and hold-date (same arg shape, docs/sonas.md §6.1).

    areaIds is optional on the wire; when the event already holds areas, they are
    re-sent so the change keeps them reserved.
    """
    arg = {"eventId": event_id, "date": to_ejson_date(date)}
    if end_date:
        arg["eventEndDate"] = to_ejson_date(end_date)
    if ceremony_date:
        arg["ceremonyDate"] = to_ejson_date(ceremony_date)
    client = _client()
    try:
        try:
            current = client.get_event(event_id)
        except Exception:
            current = {}  # date-less enquiries are invisible to the date-range read
        areas = current.get("areaIds") or current.get("reservedAreaIds")
        if areas:
            arg["areaIds"] = areas
        try:
            client.call(method, arg)
        except Exception as e:
            typer.echo(f"Error: {what}: {e}", err=True)
            raise typer.Exit(1)
    finally:
        client.close()
    typer.echo(f"{what}: done.")


@event_app.command("change-date")
def event_change_date(
    event_id: str = typer.Argument(..., help="Event document id."),
    date: str = typer.Option(..., "--date", help="New event date (YYYY-MM-DD)."),
    end_date: Optional[str] = typer.Option(None, "--end-date", help="New end date (YYYY-MM-DD)."),
    ceremony_date: Optional[str] = typer.Option(None, "--ceremony-date", help="Ceremony date (YYYY-MM-DD)."),
):
    """Move an event to a new date (eventChangeDate)."""
    _date_change("eventChangeDate", event_id, date, end_date, ceremony_date,
                 f"change date of {event_id} to {date}")


@event_app.command("hold-date")
def event_hold_date(
    event_id: str = typer.Argument(..., help="Event document id."),
    date: str = typer.Option(..., "--date", help="Date to hold (YYYY-MM-DD)."),
    end_date: Optional[str] = typer.Option(None, "--end-date", help="End date (YYYY-MM-DD)."),
    ceremony_date: Optional[str] = typer.Option(None, "--ceremony-date", help="Ceremony date (YYYY-MM-DD)."),
):
    """Hold a date for an enquiry (eventHoldDate); sets the date and DateOnHold status."""
    _date_change("eventHoldDate", event_id, date, end_date, ceremony_date,
                 f"hold {date} for {event_id}")


@event_app.command("exhaust-enquiry")
def event_exhaust_enquiry(
    event_id: str = typer.Argument(..., help="Event document id."),
    data: Optional[str] = typer.Option(
        None, "--data", help='Optional doc JSON: {"reasonNotBookedId", "venueBookedId"}.'
    ),
    file: Optional[str] = typer.Option(None, "--file", "-f", help="Read the doc JSON from a file."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Mark an enquiry as exhausted (eventExhaustEnquiry); both doc keys are optional."""
    doc = _read_data(data, file) if (data is not None or file is not None) else {}
    _do_call("eventExhaustEnquiry", {"eventId": event_id, "doc": doc},
             f"exhaust enquiry {event_id}", output_json=output_json)


@event_app.command("rename")
def event_rename(
    event_id: str = typer.Argument(..., help="Event document id."),
    name: str = typer.Option(..., "--name", help="New event name."),
):
    """Rename an event (eventUpdateGeneralSection $set name)."""
    _do_call("eventUpdateGeneralSection",
             {"modifier": {"$set": {"name": name}}, "eventId": event_id},
             f"rename {event_id} to {name!r}")


@event_app.command("delete")
def event_delete(
    event_id: str = typer.Argument(..., help="Event document id."),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
):
    """Delete an event (eventDelete); undo via `event restore` is permission-gated."""
    _do_call("eventDelete", {"eventId": event_id}, f"delete event {event_id}",
             confirm=f"Delete event {event_id}?", yes=yes)


@event_app.command("restore")
def event_restore(
    event_id: str = typer.Argument(..., help="Event document id."),
):
    """Restore a deleted event (eventRestore); needs the events.general.to-confirmed-pending
    permission (unverified; see docs/sonas.md §6)."""
    _do_call("eventRestore", {"eventId": event_id}, f"restore event {event_id}")


@event_app.command("cancel")
def event_cancel(
    event_id: str = typer.Argument(..., help="Event document id."),
    reason: str = typer.Option(..., "--reason", help="Cancellation reason slug (reasonSlug)."),
    note: Optional[str] = typer.Option(None, "--note", help="Cancellation note."),
    data: Optional[str] = typer.Option(
        None, "--data",
        help='Extra keys JSON, e.g. {"cancelFutureCharges": false, "revokePortalAccess": true}.',
    ),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
):
    """Cancel an event with its workflow (eventCancelWithWorkflow): may stop future
    charges and revoke portal access (unverified; see docs/sonas.md §6). This is the
    only cancellation path: a plain change-status to Cancelled is a silent no-op."""
    arg = {"eventId": event_id, "reasonSlug": reason,
           "cancelFutureCharges": True, "revokePortalAccess": False}
    if note is not None:
        arg["note"] = note
    if data is not None:
        arg.update(_read_data(data, None))
    _do_call("eventCancelWithWorkflow", arg, f"cancel event {event_id}",
             confirm=f"Cancel event {event_id} with the cancellation workflow?", yes=yes)


# ----------------------------------------------------------------------
# guest
# ----------------------------------------------------------------------

# EventGuestAttendingStatusEnum (docs/sonas.md §7).
GUEST_ATTENDING = {0: "Yes", 1: "No", 2: "Maybe"}


def _ejson_ms(key: str):
    """Unwrap a guest's EJSON epoch-ms stamp ({"$date": ms}) for parse_epoch_ms."""
    def get(record):
        value = record.get(key)
        if isinstance(value, dict):
            return value.get("$date")
        return value
    return get


# Named-guest records map onto inetOrgPerson for LDIF export. Guests carry a
# firstname/lastname pair and a Mongo _id; createdAt/updatedAt are EJSON
# epoch-ms stamps when present and are left unset otherwise.
_GUEST_PERSON_MAP = PersonMap(
    attrs={
        "givenName": "firstname",
        "sn": "lastname",
    },
    id_key="_id",
    created=_ejson_ms("createdAt"),
    modified=_ejson_ms("updatedAt"),
    parse_dt=parse_epoch_ms,
)


def _guest_sink(config: dict) -> LdifSink:
    """Build the LdifSink for the guest people command from the config."""
    site_cfg = resolve_account(config, "sonas", account())
    return LdifSink(
        pm=_GUEST_PERSON_MAP,
        site="sonas",
        tz=resolve_timezone(config, site_cfg),
        base_dn=resolve_base_dn(config),
    )


@guest_app.command("list")
def guest_list(
    event_id: str = typer.Argument(..., help="Event document id."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
    ldif: bool = typer.Option(
        False, "--ldif", help="Output LDIF (inetOrgPerson) instead of a table."),
):
    """List an event's named guests (guests publication).

    Named guests are a separate record from the headcount: an event can count
    120 guests (currentMain, see `guest set-numbers`) while naming only the
    couple here.
    """
    config = read_config(find_config())
    client = _make_client(config)
    _require_event(client, event_id)
    try:
        guests = client.read_pub("guests", [event_id], collection="guests")
    except Exception as e:
        typer.echo(f"Error fetching guests: {e}", err=True)
        raise typer.Exit(1)
    finally:
        client.close()
    guests = asof.current_state(guests, "guests")
    if ldif:
        emit_list(guests, [], "guest", output_json, ldif=_guest_sink(config))
        return
    if not output_json:
        for g in guests:
            g["attendingStatus"] = GUEST_ATTENDING.get(
                g.get("attendingStatus"), s(g.get("attendingStatus")))
    _emit(guests, output_json, columns=[
        ("Id", "_id"), ("First", "firstname"), ("Last", "lastname"),
        ("Type", "type"), ("Category", "category"),
        ("Attending", "attendingStatus"), ("Role", "role"), ("Notes", "notes"),
    ], what="guest")


@guest_app.command("add")
def guest_add(
    event_id: str = typer.Argument(..., help="Event document id."),
    firstname: Optional[str] = typer.Option(None, "--firstname", help="Guest first name."),
    lastname: Optional[str] = typer.Option(None, "--lastname", help="Guest last name."),
    role: Optional[str] = typer.Option(None, "--role", help="Free-text role (e.g. Bride)."),
    category: str = typer.Option("Main", "--category", help="Main or Additional."),
    type_: str = typer.Option("Adult", "--type",
                              help="Adult, Teenager, Child, Infant or Supplier."),
    attending: int = typer.Option(0, "--attending", help="0 Yes, 1 No, 2 Maybe."),
    data: Optional[str] = typer.Option(None, "--data", help="Full data as JSON (flags overlay it)."),
    file: Optional[str] = typer.Option(None, "--file", "-f", help="Read the data JSON from a file."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Add a named guest (eventAddGuest).

    The data keys are EventGuestAddSchema (docs/sonas.md §6.1): firstname and
    lastname required; role, category, type, attendingStatus optional with the
    flag defaults.
    """
    doc = _read_data(data, file) if (data is not None or file is not None) else {}
    for key, value in (("firstname", firstname), ("lastname", lastname), ("role", role)):
        if value is not None:
            doc[key] = value
    doc.setdefault("category", category)
    doc.setdefault("type", type_)
    doc.setdefault("attendingStatus", attending)
    missing = [k for k in ("firstname", "lastname") if not doc.get(k)]
    if missing:
        typer.echo(f"Error: data is missing required key(s): {', '.join(missing)}.", err=True)
        raise typer.Exit(1)
    _do_call("eventAddGuest", {"eventId": event_id, "data": doc},
             "add guest", output_json=output_json)


@guest_app.command("update")
def guest_update(
    event_id: str = typer.Argument(..., help="Event document id."),
    guest_id: str = typer.Argument(..., help="Guest document id (from `guest list`)."),
    data: Optional[str] = typer.Option(
        None, "--data",
        help='Mongo modifier JSON, e.g. {"$set": {"role": "Bride", "email": "a@example.com"}}.'),
    file: Optional[str] = typer.Option(None, "--file", "-f", help="Read the modifier JSON from a file."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Update a named guest (eventUpdateGuest); the modifier keys are
    EventGuestCoreSchema fields (docs/sonas.md §6.1)."""
    _do_call("eventUpdateGuest",
             {"eventId": event_id, "guestId": guest_id, "modifier": _read_data(data, file)},
             f"update guest {guest_id}", output_json=output_json)


@guest_app.command("delete")
def guest_delete(
    event_id: str = typer.Argument(..., help="Event document id."),
    guest_id: str = typer.Argument(..., help="Guest document id (from `guest list`)."),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
):
    """Delete a named guest (eventDeleteGuest)."""
    _do_call("eventDeleteGuest", {"eventId": event_id, "guestId": guest_id},
             f"delete guest {guest_id}",
             confirm=f"Delete guest {guest_id} from event {event_id}?", yes=yes)


@guest_app.command("set-numbers")
def guest_set_numbers(
    event_id: str = typer.Argument(..., help="Event document id."),
    adults: Optional[int] = typer.Option(None, "--adults", help="currentMain.adults."),
    teenagers: Optional[int] = typer.Option(None, "--teenagers", help="currentMain.teenagers."),
    children: Optional[int] = typer.Option(None, "--children", help="currentMain.children."),
    infants: Optional[int] = typer.Option(None, "--infants", help="currentMain.infants."),
    suppliers: Optional[int] = typer.Option(None, "--suppliers", help="currentMain.suppliers."),
    data: Optional[str] = typer.Option(
        None, "--data",
        help='Full Mongo modifier JSON, e.g. {"$set": {"currentMain.adults": 80}} '
             "(flags overlay it; currentAdditional needs config.allowAdditionalGuests)."),
    file: Optional[str] = typer.Option(None, "--file", "-f", help="Read the modifier JSON from a file."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Set the event headcount (eventUpdateGuestNumbers): the currentMain /
    currentAdditional counts shown by `event list`, independent of the named
    guest list."""
    modifier = _read_data(data, file) if (data is not None or file is not None) else {}
    sets = modifier.setdefault("$set", {})
    for key, value in (("adults", adults), ("teenagers", teenagers), ("children", children),
                       ("infants", infants), ("suppliers", suppliers)):
        if value is not None:
            sets[f"currentMain.{key}"] = value
    if not sets and len(modifier) == 1:
        typer.echo("Error: nothing to set; pass count flags or --data.", err=True)
        raise typer.Exit(1)
    if not sets:
        del modifier["$set"]
    _do_call("eventUpdateGuestNumbers", {"eventId": event_id, "modifier": modifier},
             f"set guest numbers on {event_id}", output_json=output_json)


# ----------------------------------------------------------------------
# timeline
# ----------------------------------------------------------------------

# TimelineEntryTypeEnum (docs/sonas.md §7).
TIMELINE_TYPE = {0: "Relative", 1: "Absolute", 2: "RelativeToCeremony"}


def _event_timeline(client, event_id: str) -> dict:
    """The event's timeline doc ({entries: [...]}, collection `timelines`),
    delivered by the multi-cursor eventBasicInfo pub; {} when the event has
    no timeline yet."""
    docs = client.read_pub("eventBasicInfo", [event_id], collection="timelines")
    for doc in docs:
        if doc.get("eventId") == event_id:
            return doc
    return {}


def _datetime_ejson(value: str) -> dict:
    """Parse an ISO datetime (e.g. 2031-11-20T15:00 or '... +10:00') to EJSON;
    a naive value counts as UTC. The app renders times in the venue timezone."""
    from datetime import datetime, timezone
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as e:
        typer.echo(f"Error: invalid datetime {value!r}: {e}", err=True)
        raise typer.Exit(2)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return {"$date": int(dt.timestamp() * 1000)}


@timeline_app.command("list")
def timeline_list(
    event_id: str = typer.Argument(..., help="Event document id."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """List an event's timeline entries (the `timelines` doc carried by the
    eventBasicInfo publication)."""
    client = _client()
    _require_event(client, event_id)
    try:
        entries = _event_timeline(client, event_id).get("entries") or []
    except Exception as e:
        typer.echo(f"Error fetching timeline: {e}", err=True)
        raise typer.Exit(1)
    finally:
        client.close()
    entries = asof.current_state(entries, "timeline entries")
    if not output_json:
        for entry in entries:
            entry["type"] = TIMELINE_TYPE.get(entry.get("type"), s(entry.get("type")))
            time_v = entry.get("time")
            if isinstance(time_v, dict) and "$date" in time_v:
                from datetime import datetime, timezone
                entry["time"] = datetime.fromtimestamp(
                    time_v["$date"] / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    _emit(entries, output_json, columns=[
        ("Id", "_id"), ("Type", "type"), ("Time", "time"),
        ("Offset(min)", "relOffsetMinutes"), ("Duration(min)", "durationMinutes"),
        ("Description", "description"), ("Section", "sectionId"),
    ], what="entry")


@timeline_app.command("add")
def timeline_add(
    event_id: str = typer.Argument(..., help="Event document id."),
    description: Optional[str] = typer.Option(None, "--description", help="Entry name."),
    time: Optional[str] = typer.Option(
        None, "--time", help="Absolute entry: ISO datetime (naive = UTC)."),
    after: Optional[str] = typer.Option(
        None, "--after", help="Relative entry: the entry id this one follows (timeRefId)."),
    offset_minutes: Optional[int] = typer.Option(
        None, "--offset-minutes", help="Relative entry: minutes after (negative = before) --after."),
    duration: Optional[int] = typer.Option(None, "--duration", help="Duration in minutes."),
    notes: Optional[str] = typer.Option(None, "--notes", help="Entry notes."),
    section: str = typer.Option("timeline", "--section", help="EventSectionEnum slug (docs/sonas.md §7)."),
    data: Optional[str] = typer.Option(None, "--data", help="Full entry as JSON (flags overlay it)."),
    file: Optional[str] = typer.Option(None, "--file", "-f", help="Read the entry JSON from a file."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Add a timeline entry (eventAddNewTimelineEntry); prints the entry id.

    The entry is TimelineEntryCreateSchema (docs/sonas.md §6.1): absolute
    (--time) or relative to another entry (--after + --offset-minutes).
    """
    entry = _read_data(data, file) if (data is not None or file is not None) else {}
    if description is not None:
        entry["description"] = description
    if time is not None:
        entry.setdefault("type", 1)
        entry["time"] = _datetime_ejson(time)
    if after is not None:
        entry.setdefault("type", 0)
        entry["timeRefId"] = after
    if offset_minutes is not None:
        entry["relOffsetMinutes"] = offset_minutes
    if duration is not None:
        entry["durationMinutes"] = duration
    if notes is not None:
        entry["notes"] = notes
    entry.setdefault("sectionId", section)
    if not entry.get("description"):
        typer.echo("Error: the entry needs a --description.", err=True)
        raise typer.Exit(1)
    if "type" not in entry:
        typer.echo("Error: pass --time (absolute) or --after + --offset-minutes (relative).",
                   err=True)
        raise typer.Exit(1)
    _do_call("eventAddNewTimelineEntry", {"eventId": event_id, "entry": entry},
             "add timeline entry", output_json=output_json)


@timeline_app.command("update")
def timeline_update(
    event_id: str = typer.Argument(..., help="Event document id."),
    entry_id: str = typer.Argument(..., help="Entry id (from `timeline list`)."),
    data: Optional[str] = typer.Option(
        None, "--data",
        help="The replacement entry as JSON (a full TimelineEntryCreateSchema "
             "document, not a Mongo modifier)."),
    file: Optional[str] = typer.Option(None, "--file", "-f", help="Read the entry JSON from a file."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Replace a timeline entry (eventEditTimelineEntry)."""
    _do_call("eventEditTimelineEntry",
             {"eventId": event_id, "entryId": entry_id, "entry": _read_data(data, file)},
             f"update timeline entry {entry_id}", output_json=output_json)


@timeline_app.command("delete")
def timeline_delete(
    event_id: str = typer.Argument(..., help="Event document id."),
    entry_id: str = typer.Argument(..., help="Entry id (from `timeline list`)."),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
):
    """Delete a timeline entry (eventDeleteTimelineEntry)."""
    _do_call("eventDeleteTimelineEntry",
             {"eventId": event_id, "timelineEntryId": entry_id},
             f"delete timeline entry {entry_id}",
             confirm=f"Delete timeline entry {entry_id} from event {event_id}?", yes=yes)


@timeline_app.command("import")
def timeline_import(
    event_id: str = typer.Argument(..., help="Event document id."),
    timeline_id: str = typer.Argument(
        ..., help="Template timeline id (an eventId-less doc in the `timelines` pub)."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Append a tenant timeline template's entries to the event
    (eventImportTimeline). Entries it adds get fresh ids; remove them one by
    one with `timeline delete`."""
    _do_call("eventImportTimeline", {"eventId": event_id, "timelineId": timeline_id},
             f"import timeline {timeline_id}", output_json=output_json)


# ----------------------------------------------------------------------
# note
# ----------------------------------------------------------------------


@note_app.command("list")
def note_list(
    event_id: str = typer.Argument(..., help="Event document id."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """List an event's staff notes (eventNotes publication, collection `notes`)."""
    _pub_list("eventNotes", [event_id], columns=[
        ("Id", "_id"), ("Created", "createdAt"), ("Section", "sectionId"),
        ("Author", "author"), ("Text", "text"),
    ], output_json=output_json, what="note", collection="notes", event_id=event_id,
        created="createdAt")


@note_app.command("add")
def note_add(
    event_id: str = typer.Argument(..., help="Event document id."),
    text: str = typer.Option(..., "--text", help="Note text."),
    section: Optional[str] = typer.Option(
        None, "--section", help="EventSectionEnum slug (docs/sonas.md §7); default notes."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Add a staff note (eventAddNote); prints the note id."""
    arg = {"eventId": event_id, "text": text}
    if section is not None:
        arg["sectionId"] = section
    _do_call("eventAddNote", arg, "add note", output_json=output_json)


@note_app.command("edit")
def note_edit(
    note_id: str = typer.Argument(..., help="Note document id (from `note list`)."),
    text: str = typer.Option(..., "--text", help="New note text."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Replace a note's text (eventUpdateNote)."""
    _do_call("eventUpdateNote", {"noteId": note_id, "text": text},
             f"edit note {note_id}", output_json=output_json)


@note_app.command("delete")
def note_delete(
    note_id: str = typer.Argument(..., help="Note document id (from `note list`)."),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
):
    """Delete a note (eventRemoveNote)."""
    _do_call("eventRemoveNote", {"noteId": note_id}, f"delete note {note_id}",
             confirm=f"Delete note {note_id}?", yes=yes)


# ----------------------------------------------------------------------
# transaction / invoice (financial records)
# ----------------------------------------------------------------------

# Transaction and financial-record enums (docs/sonas.md §7).
TRANSACTION_KIND = {1: "Charge", 2: "Payment", 3: "Refund", 4: "Discount",
                    5: "PaymentMethodFee"}
TRANSACTION_STATUS = {0: "Accepted", 1: "Failed", 2: "Cancelled", 3: "Pending"}
PAYMENT_METHOD = {0: "Cash", 1: "Card", 2: "Cheque", 3: "Transfer", 4: "DirectDebit",
                  5: "EscrowAccount", 6: "OnlineBankTransfer", 100: "Other"}
FINANCIAL_RECORD_TYPE = {1: "Proforma", 2: "Invoice", 3: "CreditNote"}
FINANCIAL_RECORD_STATUS = {1: "Valid", 4: "Cancelled", 5: "Draft"}


def _name_enums(items: list, fields: dict) -> None:
    """Replace integer enum fields with their names for table rendering;
    ``fields`` maps a field name to its enum table."""
    for item in items:
        for key, names in fields.items():
            if key in item:
                item[key] = names.get(item[key], s(item[key]))


@transaction_app.command("list")
def transaction_list(
    event_id: str = typer.Argument(..., help="Event document id."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """List an event's transactions (eventTransactions publication, collection
    `transactions`): charges, payments, refunds, discounts."""
    client = _client()
    _require_event(client, event_id)
    try:
        items = client.read_pub("eventTransactions", [event_id], collection="transactions")
    except Exception as e:
        typer.echo(f"Error fetching transactions: {e}", err=True)
        raise typer.Exit(1)
    finally:
        client.close()
    items = _asof_filter(items, "transaction")
    if not output_json:
        _name_enums(items, {"kind": TRANSACTION_KIND, "status": TRANSACTION_STATUS,
                            "method": PAYMENT_METHOD})
    _emit(items, output_json, columns=[
        ("Id", "_id"), ("Kind", "kind"), ("Status", "status"), ("Amount", "amount"),
        ("Method", "method"), ("Due", "dueDate"), ("Description", "description"),
    ], what="transaction")


@transaction_app.command("charge")
def transaction_charge(
    event_id: str = typer.Argument(..., help="Event document id."),
    data: Optional[str] = typer.Option(
        None, "--data",
        help='The doc as JSON: {"amount": N, "dueDate": {"$date": ms}, '
             '"description"?, "categoryId"?, "sectionId"?}.'),
    file: Optional[str] = typer.Option(None, "--file", "-f", help="Read the doc JSON from a file."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Create a charge on an event (makeChargeTransaction). The doc is
    CreateChargeSchema (docs/sonas.md §6.1): amount (>= 0) and dueDate (EJSON
    date) required; description, categoryId (a charges-tag category) and
    sectionId (manual-charge slug; required once categoryId is set) optional.
    Touches event finance (unverified; see docs/sonas.md §6)."""
    _do_call("makeChargeTransaction",
             {"eventId": event_id, "doc": _read_data(data, file)},
             f"charge event {event_id}", output_json=output_json)


@transaction_app.command("payment")
def transaction_payment(
    event_id: str = typer.Argument(..., help="Event document id."),
    record: str = typer.Option(..., "--record",
                               help="Financial-record id the payment settles (from `invoice list`)."),
    method: str = typer.Option(..., "--method",
                               help="Payment method, name or number (Cash, Card, Cheque, "
                                    "Transfer, DirectDebit, EscrowAccount, OnlineBankTransfer, Other)."),
    amount: float = typer.Option(..., "--amount", help="Amount paid."),
    description: Optional[str] = typer.Option(None, "--description", help="Payment description."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Record a payment against a financial record (createPaymentTransaction);
    the method's shape is these flat typed args, no doc. Touches event finance
    (unverified; see docs/sonas.md §6)."""
    arg = {"eventId": event_id, "financialRecordId": record,
           "method": _parse_enum(method, PAYMENT_METHOD, "payment method"),
           "amount": amount}
    if description is not None:
        arg["description"] = description
    _do_call("createPaymentTransaction", arg, f"record payment on {event_id}",
             output_json=output_json)


@transaction_app.command("refund")
def transaction_refund(
    event_id: str = typer.Argument(..., help="Event document id."),
    data: Optional[str] = typer.Option(
        None, "--data",
        help='The doc as JSON: {"amount": N, "dueDate": {"$date": ms}, "method": n, '
             '"financialRecordId": id, "description"?}.'),
    file: Optional[str] = typer.Option(None, "--file", "-f", help="Read the doc JSON from a file."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Create a refund (makeRefundTransaction). The doc is CreateRefundSchema
    (docs/sonas.md §6.1): amount (>= 0), dueDate (EJSON date), method
    (PaymentMethod number, §7) and financialRecordId required; description
    optional. Touches event finance (unverified; see docs/sonas.md §6)."""
    _do_call("makeRefundTransaction",
             {"eventId": event_id, "doc": _read_data(data, file)},
             f"refund on event {event_id}", output_json=output_json)


@transaction_app.command("discount")
def transaction_discount(
    event_id: str = typer.Argument(..., help="Event document id."),
    data: Optional[str] = typer.Option(
        None, "--data",
        help='The doc as JSON: {"amount": N, "dueDate": {"$date": ms}, "description"?}.'),
    file: Optional[str] = typer.Option(None, "--file", "-f", help="Read the doc JSON from a file."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Create a discount (makeDiscountTransaction). The doc is
    CreateDiscountSchema (docs/sonas.md §6.1): amount (>= 0) and dueDate (EJSON
    date) required; description optional. Touches event finance
    (unverified; see docs/sonas.md §6)."""
    _do_call("makeDiscountTransaction",
             {"eventId": event_id, "doc": _read_data(data, file)},
             f"discount on event {event_id}", output_json=output_json)


@transaction_app.command("approve")
def transaction_approve(
    transaction_id: str = typer.Argument(..., help="Transaction id (from `transaction list`)."),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Approve a transaction (approveTransaction)
    (unverified; see docs/sonas.md §6)."""
    _do_call("approveTransaction", {"transactionId": transaction_id},
             f"approve transaction {transaction_id}",
             confirm=f"Approve transaction {transaction_id}?", yes=yes,
             output_json=output_json)


@transaction_app.command("cancel")
def transaction_cancel(
    transaction_id: str = typer.Argument(..., help="Transaction id (from `transaction list`)."),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Cancel a transaction (cancelTransaction); the server refuses
    non-cancellable ones, and cancelling a payment or refund needs the
    void-credit permission (unverified; see docs/sonas.md §6)."""
    _do_call("cancelTransaction", {"transactionId": transaction_id},
             f"cancel transaction {transaction_id}",
             confirm=f"Cancel transaction {transaction_id}?", yes=yes,
             output_json=output_json)


def _financial_records(client, event_id: str) -> list:
    return client.read_pub("eventFinancialRecords", [event_id],
                           collection="financial-records")


@invoice_app.command("list")
def invoice_list(
    event_id: str = typer.Argument(..., help="Event document id."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """List an event's financial records (eventFinancialRecords publication,
    collection `financial-records`): proformas, invoices, credit notes."""
    client = _client()
    _require_event(client, event_id)
    try:
        records = _financial_records(client, event_id)
    except Exception as e:
        typer.echo(f"Error fetching financial records: {e}", err=True)
        raise typer.Exit(1)
    finally:
        client.close()
    records = _asof_filter(records, "financial record")
    if not output_json:
        _name_enums(records, {"type": FINANCIAL_RECORD_TYPE,
                              "status": FINANCIAL_RECORD_STATUS})
    _emit(records, output_json, columns=[
        ("Id", "_id"), ("Ref", "reference"), ("Type", "type"), ("Status", "status"),
        ("Date", "date"), ("Due", "dueDate"), ("Total", "totalAmount"),
        ("Paid", "totalPaid"),
    ], what="record")


@invoice_app.command("get")
def invoice_get(
    event_id: str = typer.Argument(..., help="Event document id."),
    record_id: str = typer.Argument(..., help="Financial-record id (from `invoice list`)."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Show one financial record, with its line entries (filtered from the
    eventFinancialRecords publication)."""
    client = _client()
    _require_event(client, event_id)
    try:
        records = _financial_records(client, event_id)
    except Exception as e:
        typer.echo(f"Error fetching financial records: {e}", err=True)
        raise typer.Exit(1)
    finally:
        client.close()
    records = _asof_filter(records, "financial record")
    record = next((r for r in records if r["_id"] == record_id), None)
    if record is None:
        typer.echo(f"Error: record {record_id} not found on event {event_id}.", err=True)
        raise typer.Exit(1)
    _emit_record(record, output_json)


@invoice_app.command("pdf")
def invoice_pdf(
    record_id: str = typer.Argument(..., help="Financial-record id (from `invoice list`)."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Generate a financial record's PDF document
    (generateFinancialRecordDocument); the artifact is visible in the customer
    portal (unverified; see docs/sonas.md §6)."""
    _do_call("generateFinancialRecordDocument", {"financialRecordId": record_id},
             f"generate document for record {record_id}", output_json=output_json)


# ----------------------------------------------------------------------
# service-booking
# ----------------------------------------------------------------------

# ServiceBookingStatusEnum (docs/sonas.md §7).
SERVICE_BOOKING_STATUS = {1: "Pending", 2: "Booked", 3: "Cancelled"}


def _options_summary(booking: dict) -> str:
    parts = []
    for opt in booking.get("selectedOptions") or []:
        qty = opt.get("quantity")
        parts.append(f"{opt.get('name')} x{qty}" if qty not in (None, 1) else s(opt.get("name")))
    return ", ".join(parts)


@service_booking_app.command("list")
def service_booking_list(
    event_id: str = typer.Argument(..., help="Event document id."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """List an event's service bookings (eventServiceBookings publication; it
    also carries the referenced `services` docs, used here for the names)."""
    client = _client()
    _require_event(client, event_id)
    try:
        docs = client.read_pub("eventServiceBookings", [event_id])
    except Exception as e:
        typer.echo(f"Error fetching service bookings: {e}", err=True)
        raise typer.Exit(1)
    finally:
        client.close()
    services = {d["_id"]: d.get("name") for d in docs if d["_collection"] == "services"}
    bookings = [d for d in docs if d["_collection"] == "service-bookings"]
    bookings = _asof_filter(bookings, "service booking")
    if output_json:
        _emit(bookings, True, what="booking")
        return
    for b in bookings:
        b["service"] = services.get(b.get("serviceId"), s(b.get("serviceId")))
        b["options"] = _options_summary(b)
        b["status"] = SERVICE_BOOKING_STATUS.get(b.get("status"), s(b.get("status")))
    _emit(bookings, False, columns=[
        ("Id", "_id"), ("Service", "service"), ("Status", "status"),
        ("Options", "options"), ("From", "from"), ("To", "to"),
    ], what="booking")


def _selected_options(client, service_id: str, option_specs: List[str]) -> list:
    """Expand ``optionId[:qty]`` specs against the service's option docs into
    SelectedOptionSchema objects (docs/sonas.md §6.1)."""
    rows, _info = client.read_tabular("ServiceList", data_pub="tabular_genericPub",
                                      selector={"_id": service_id}, limit=2)
    if not rows:
        typer.echo(f"Error: service {service_id} not found in ServiceList.", err=True)
        raise typer.Exit(1)
    available = {o["_id"]: o for o in rows[0].get("options") or []}
    selected = []
    for spec in option_specs:
        opt_id, _, qty = spec.partition(":")
        if opt_id not in available:
            choices = ", ".join(f"{i} ({o.get('name')})" for i, o in available.items())
            typer.echo(f"Error: option {opt_id} not on service {service_id}; "
                       f"available: {choices}.", err=True)
            raise typer.Exit(1)
        option = {k: v for k, v in available[opt_id].items()
                  if k in ("_id", "name", "internalName", "description", "price")}
        option["quantity"] = int(qty) if qty else 1
        selected.append(option)
    return selected


@service_booking_app.command("add")
def service_booking_add(
    event_id: str = typer.Argument(..., help="Event document id."),
    service: Optional[str] = typer.Option(None, "--service", help="Service id (from the catalog)."),
    option: List[str] = typer.Option(
        [], "--option", help="Option to book, as optionId or optionId:quantity; repeatable."),
    data: Optional[str] = typer.Option(
        None, "--data", help="Full arg JSON (selectedOptions, questions); flags overlay it."),
    file: Optional[str] = typer.Option(None, "--file", "-f", help="Read the arg JSON from a file."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Book a service on an event (eventAddServiceBooking).

    selectedOptions needs at least one option with quantity >= 1; --option
    expands ids against the service's catalog doc. questions, when sent, are
    {question, answer?} pairs (--data).
    """
    arg = _read_data(data, file) if (data is not None or file is not None) else {}
    arg["eventId"] = event_id
    if service is not None:
        arg["serviceId"] = service
    if not arg.get("serviceId"):
        typer.echo("Error: pass --service (or a serviceId key in --data).", err=True)
        raise typer.Exit(1)
    client = _client()
    try:
        if option:
            arg["selectedOptions"] = _selected_options(client, arg["serviceId"], option)
        if not arg.get("selectedOptions"):
            typer.echo("Error: pass --option (or selectedOptions in --data).", err=True)
            raise typer.Exit(1)
        arg.setdefault("questions", [])
        try:
            result = client.call("eventAddServiceBooking", arg)
        except Exception as e:
            typer.echo(f"Error: add service booking: {e}", err=True)
            raise typer.Exit(1)
    finally:
        client.close()
    if output_json:
        typer.echo(json.dumps(result if result is not None else {"ok": True}, indent=2))
        return
    typer.echo("add service booking: done.")
    if result is not None:
        typer.echo(str(result))


@service_booking_app.command("edit")
def service_booking_edit(
    event_id: str = typer.Argument(..., help="Event document id."),
    booking_id: str = typer.Argument(..., help="Booking id (from `service-booking list`)."),
    option: List[str] = typer.Option(
        [], "--option", help="Replacement option, as optionId or optionId:quantity; repeatable."),
    data: Optional[str] = typer.Option(
        None, "--data", help="Full arg JSON (selectedOptions, questions); flags overlay it."),
    file: Optional[str] = typer.Option(None, "--file", "-f", help="Read the arg JSON from a file."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Replace a booking's selected options (eventEditServiceBooking); the
    selectedOptions array is a full replacement, like `timeline update`."""
    arg = _read_data(data, file) if (data is not None or file is not None) else {}
    arg["eventId"] = event_id
    arg["bookingId"] = booking_id
    client = _client()
    try:
        if option:
            bookings = client.read_pub("eventServiceBookings", [event_id],
                                       collection="service-bookings")
            booking = next((b for b in bookings if b["_id"] == booking_id), None)
            if booking is None:
                typer.echo(f"Error: booking {booking_id} not found on event {event_id}.",
                           err=True)
                raise typer.Exit(1)
            arg["selectedOptions"] = _selected_options(client, booking["serviceId"], option)
        if not arg.get("selectedOptions"):
            typer.echo("Error: pass --option (or selectedOptions in --data).", err=True)
            raise typer.Exit(1)
        arg.setdefault("questions", [])
        try:
            result = client.call("eventEditServiceBooking", arg)
        except Exception as e:
            typer.echo(f"Error: edit service booking: {e}", err=True)
            raise typer.Exit(1)
    finally:
        client.close()
    if output_json:
        typer.echo(json.dumps(result if result is not None else {"ok": True}, indent=2))
        return
    typer.echo(f"edit service booking {booking_id}: done.")


@service_booking_app.command("cancel")
def service_booking_cancel(
    event_id: str = typer.Argument(..., help="Event document id."),
    booking_id: str = typer.Argument(..., help="Booking id (from `service-booking list`)."),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
):
    """Cancel a service booking (eventCancelServiceBooking). The booking doc
    stays, status Cancelled; there is no delete method."""
    _do_call("eventCancelServiceBooking", {"eventId": event_id, "bookingId": booking_id},
             f"cancel service booking {booking_id}",
             confirm=f"Cancel service booking {booking_id}?", yes=yes)


@service_booking_app.command("confirm")
def service_booking_confirm(
    event_id: str = typer.Argument(..., help="Event document id."),
    booking_id: str = typer.Argument(..., help="Booking id (from `service-booking list`)."),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
):
    """Confirm a service booking (eventConfirmServiceBooking), Pending to Booked;
    may notify the supplier and raise the service's deposit charge
    (unverified; see docs/sonas.md §6)."""
    _do_call("eventConfirmServiceBooking", {"eventId": event_id, "bookingId": booking_id},
             f"confirm service booking {booking_id}",
             confirm=f"Confirm service booking {booking_id}?", yes=yes)


# ----------------------------------------------------------------------
# message / document / terms / activity
# ----------------------------------------------------------------------

# Message and terms enums (docs/sonas.md §7).
MESSAGE_STATUS = {0: "Incoming", 1: "Received", 2: "Outgoing", 3: "Sent",
                  4: "Delivered", 7: "Opened", 9: "Draft"}
MESSAGE_TRANSPORT = {0: "Internal", 1: "Email"}
TERMS_STATUS = {0: "Waiting", 1: "Accepted", 2: "Rejected"}


@message_app.command("list")
def message_list(
    event_id: str = typer.Argument(..., help="Event document id."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """List an event's messages (eventMessages publication, collection
    `messages`; the pub also carries the attachment `files` docs, listed by
    `document list`)."""
    client = _client()
    _require_event(client, event_id)
    try:
        messages = client.read_pub("eventMessages", [event_id], collection="messages")
    except Exception as e:
        typer.echo(f"Error fetching messages: {e}", err=True)
        raise typer.Exit(1)
    finally:
        client.close()
    messages = _asof_filter(messages, "message")
    messages.sort(key=lambda m: (m.get("createdAt") or {}).get("$date", 0)
                  if isinstance(m.get("createdAt"), dict) else 0)
    if not output_json:
        _name_enums(messages, {"status": MESSAGE_STATUS, "transport": MESSAGE_TRANSPORT})
    _emit(messages, output_json, columns=[
        ("Id", "_id"), ("Created", "createdAt"), ("Status", "status"),
        ("Transport", "transport"), ("Author", "author"), ("Subject", "subject"),
    ], what="message")


@message_app.command("send")
def message_send(
    event_id: str = typer.Argument(..., help="Event document id."),
    template: str = typer.Option(..., "--template", help="Email template id (from `template list`)."),
    user: str = typer.Option(..., "--user", help="Recipient user id (a customer on the event)."),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Send an email template to an event customer (eventSendEmailTemplate).
    This sends real mail to the recipient (unverified; see docs/sonas.md §6)."""
    _do_call("eventSendEmailTemplate",
             {"templateId": template, "eventId": event_id, "userId": user},
             f"send template {template} to user {user}",
             confirm=f"Send email template {template} to user {user} on event "
                     f"{event_id}? Real mail goes out.",
             yes=yes, output_json=output_json)


@document_app.command("list")
def document_list(
    event_id: str = typer.Argument(..., help="Event document id."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """List an event's document files (eventDocs publication, collection
    `files`; its second parameter is the event doc's `documents` id array,
    fetched here via eventBasicInfo, as the app does)."""
    client = _client()
    _require_event(client, event_id)
    try:
        basic = client.read_pub("eventBasicInfo", [event_id])
        event = next((d for d in basic if d["_collection"] == "events"), {})
        files = client.read_pub("eventDocs", [event_id, event.get("documents") or []],
                                collection="files")
    except Exception as e:
        typer.echo(f"Error fetching documents: {e}", err=True)
        raise typer.Exit(1)
    finally:
        client.close()
    files = _asof_filter(files, "document")
    _emit(files, output_json, columns=[
        ("Id", "_id"), ("Name", "displayName"), ("Type", "type"),
        ("Content-Type", "contentType"), ("Size", "size"), ("Created", "createdAt"),
    ], what="document")


@document_app.command("delete")
def document_delete(
    doc_id: str = typer.Argument(
        ..., help="Documents-container id (the file's containerId, in `document list --json`)."),
    file_id: str = typer.Argument(..., help="File id (from `document list`)."),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
):
    """Delete a document file (eventDeleteDoc)
    (unverified; see docs/sonas.md §6)."""
    _do_call("eventDeleteDoc", {"docId": doc_id, "fileId": file_id},
             f"delete document {file_id}",
             confirm=f"Delete document {file_id}?", yes=yes)


@terms_app.command("list")
def terms_list(
    event_id: str = typer.Argument(..., help="Event document id."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """List an event's terms-and-conditions records (eventTermsAndConditions
    publication, collection `terms-and-conditions`)."""
    client = _client()
    _require_event(client, event_id)
    try:
        terms = client.read_pub("eventTermsAndConditions", [event_id],
                                collection="terms-and-conditions")
    except Exception as e:
        typer.echo(f"Error fetching terms: {e}", err=True)
        raise typer.Exit(1)
    finally:
        client.close()
    terms = _asof_filter(
        terms, "terms record",
        created=lambda d: d.get("answeredAt") or d.get("createdAt"))
    if not output_json:
        _name_enums(terms, {"status": TERMS_STATUS})
    _emit(terms, output_json, columns=[
        ("Id", "_id"), ("Name", "name"), ("Status", "status"),
        ("Required", "required"), ("Answered by", "answeredByName"),
        ("Answered", "answeredAt"),
    ], what="terms record")


@terms_app.command("accept")
def terms_accept(
    event_id: str = typer.Argument(..., help="Event document id."),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Accept all of an event's pending terms on the couple's behalf
    (termsAcceptPending); alters contract state
    (unverified; see docs/sonas.md §6)."""
    _do_call("termsAcceptPending", {"eventId": event_id},
             f"accept pending terms on {event_id}",
             confirm=f"Accept all pending terms on event {event_id}?", yes=yes,
             output_json=output_json)


@terms_app.command("pdf")
def terms_pdf(
    terms_id: str = typer.Argument(..., help="Terms record id (from `terms list`)."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Generate a terms record's PDF (termsGeneratePDF)
    (unverified; see docs/sonas.md §6)."""
    _do_call("termsGeneratePDF", {"termsId": terms_id},
             f"generate PDF for terms {terms_id}", output_json=output_json)


@terms_app.command("create")
def terms_create(
    event_id: str = typer.Argument(..., help="Event document id."),
    data: Optional[str] = typer.Option(
        None, "--data",
        help='The terms doc as JSON. Fields (termsCreate, docs/sonas.md §6): '
             'name (str), text (str, the policy body), required (bool), '
             'type/category/channel (int enums). `eventId` is added if absent.'),
    file: Optional[str] = typer.Option(None, "--file", "-f", help="Read the doc JSON from a file."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Add a terms-and-conditions record to an event (termsCreate); this is how
    a new or updated policy is put to a couple. Payload decoded statically but
    not yet live-trialed (see docs/sonas.md §6/§11)."""
    doc = _read_data(data, file)
    doc.setdefault("eventId", event_id)
    _do_call("termsCreate", {"doc": doc},
             f"create terms on event {event_id}", output_json=output_json)


@terms_app.command("answer")
def terms_answer(
    terms_id: str = typer.Argument(..., help="Terms record id (from `terms list`)."),
    answer: str = typer.Option(
        ..., "--answer",
        help="Accepted|Rejected (or 1|2): the answer recorded on the couple's behalf."),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Answer a single terms record (termsAnswer); alters contract state. The
    server accepts only Accepted (1) or Rejected (2) (decoded statically, not
    yet live-trialed; see docs/sonas.md §6/§11)."""
    by_name = {"accepted": 1, "rejected": 2}
    value = by_name.get(answer.strip().lower())
    if value is None:
        try:
            value = int(answer)
        except ValueError:
            value = None
    if value not in (1, 2):
        typer.echo("Error: --answer must be Accepted/Rejected (or 1/2).", err=True)
        raise typer.Exit(2)
    _do_call("termsAnswer", {"termsId": terms_id, "answer": value},
             f"answer terms {terms_id}",
             confirm=f"Answer terms {terms_id} with {value} "
                     f"({'Accepted' if value == 1 else 'Rejected'})?", yes=yes,
             output_json=output_json)


@terms_app.command("delete")
def terms_delete(
    terms_id: str = typer.Argument(..., help="Terms record id (from `terms list`)."),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Delete a terms record (termsDelete); alters contract state
    (unverified; see docs/sonas.md §6/§11)."""
    _do_call("termsDelete", {"termsId": terms_id},
             f"delete terms {terms_id}",
             confirm=f"Delete terms record {terms_id}?", yes=yes,
             output_json=output_json)


@activity_app.command("list")
def activity_list(
    event_id: str = typer.Argument(..., help="Event document id."),
    limit: int = typer.Option(50, "--limit", help="Newest entries to fetch."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """List an event's activity log (eventActivities publication, collection
    `activities`). Entries carry a readable `text`; `verified` shows the
    verification date, empty for unverified."""
    client = _client()
    _require_event(client, event_id)
    try:
        acts = client.read_pub("eventActivities", [event_id, limit],
                               collection="activities")
    except Exception as e:
        typer.echo(f"Error fetching activities: {e}", err=True)
        raise typer.Exit(1)
    finally:
        client.close()
    acts = _asof_filter(acts, "activity")
    acts.sort(key=lambda a: (a.get("createdAt") or {}).get("$date", 0)
              if isinstance(a.get("createdAt"), dict) else 0)
    _emit(acts, output_json, columns=[
        ("Id", "_id"), ("Created", "createdAt"), ("Text", "text"),
        ("Section", "section"), ("Verified", "verifiedDate"),
    ], what="activity")


@activity_app.command("verify")
def activity_verify(
    activity_id: str = typer.Argument(..., help="Activity id (from `activity list`)."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Mark one activity as verified (eventVerifyActivity)."""
    _do_call("eventVerifyActivity", {"activityId": activity_id},
             f"verify activity {activity_id}", output_json=output_json)


@activity_app.command("verify-all")
def activity_verify_all(
    event_id: str = typer.Argument(..., help="Event document id."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Mark all of an event's activities as verified (eventVerifyAllActivities)."""
    _do_call("eventVerifyAllActivities", {"eventId": event_id},
             f"verify all activities on {event_id}", output_json=output_json)


# ----------------------------------------------------------------------
# availability / appointment / tasting (T2 scheduling)
# ----------------------------------------------------------------------

# CalendarEventTypeEnum (docs/sonas.md §7).
CALENDAR_EVENT_TYPE = {
    0: "ShowAround", 1: "Meeting", 2: "Holiday", 3: "OpenDay", 5: "ItemDelivery",
    6: "Tasting", 7: "Maintenance", 8: "PhotoShoot", 9: "Accommodation",
    10: "Ceremony", 11: "InternalMeeting", 12: "CustomAppointment1",
    13: "CustomAppointment2", 14: "CustomAppointment3", 100: "RegularEvent",
}


@availability_app.command("list")
def availability_list(
    from_: Optional[str] = typer.Option(None, "--from", help="Windows touching on/after this date (YYYY-MM-DD)."),
    to: Optional[str] = typer.Option(None, "--to", help="Windows touching on/before this date (YYYY-MM-DD)."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """List availability windows (availabilityByDateRange publication, collection
    `availability`): the recurring slot definitions the appointment-booking
    widget offers. The pub also serves the range's calendar-events; only the
    availability docs are listed here."""
    client = _client()
    try:
        items = client.read_pub("availabilityByDateRange", _range_params(from_, to),
                                collection="availability")
    except Exception as e:
        typer.echo(f"Error fetching availability: {e}", err=True)
        raise typer.Exit(1)
    finally:
        client.close()
    items = _asof_filter(items, "availability window")
    if not output_json:
        for item in items:
            item["availableFor"] = ", ".join(
                CALENDAR_EVENT_TYPE.get(t, s(t)) for t in item.get("availableFor") or [])
            item["slots"] = len(item.get("availability") or [])
            item["exceptions"] = len(item.get("exceptions") or [])
            item["from"] = _dt_str(item.get("from"))
            item["to"] = _dt_str(item.get("to"))
    _emit(items, output_json, columns=[
        ("Id", "_id"), ("Title", "title"), ("From", "from"), ("To", "to"),
        ("For", "availableFor"), ("Slots", "slots"), ("Exceptions", "exceptions"),
        ("Venue", "venueId"),
    ], what="availability window")


@availability_app.command("create")
def availability_create(
    data: Optional[str] = typer.Option(None, "--data", help="The doc as JSON."),
    file: Optional[str] = typer.Option(None, "--file", "-f", help="Read the doc JSON from a file."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Create an availability window (createAvailability). The doc is
    AvailabilityCoreSchema (docs/sonas.md §6.2): title, availableFor, from/to,
    defaultStaffId, availability slot array, venueId, minTimeBeforeBooking.
    Windows feed the public appointment-booking widget
    (unverified; see docs/sonas.md §6)."""
    _do_call("createAvailability", {"doc": _read_data(data, file)},
             "create availability", output_json=output_json)


@availability_app.command("update")
def availability_update(
    availability_id: str = typer.Argument(..., help="Availability id (from `availability list`)."),
    data: Optional[str] = typer.Option(
        None, "--data",
        help='Mongo modifier JSON over AvailabilityUpdateSchema fields, e.g. {"$set": {"to": ...}}.'),
    file: Optional[str] = typer.Option(None, "--file", "-f", help="Read the modifier JSON from a file."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Update an availability window (updateAvailability)
    (unverified; see docs/sonas.md §6)."""
    _do_call("updateAvailability",
             {"availabilityId": availability_id, "modifier": _read_data(data, file)},
             f"update availability {availability_id}", output_json=output_json)


@availability_app.command("delete")
def availability_delete(
    availability_id: str = typer.Argument(..., help="Availability id (from `availability list`)."),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
):
    """Delete an availability window (deleteAvailability); removes its bookable
    slots from the public widget (unverified; see docs/sonas.md §6)."""
    _do_call("deleteAvailability", {"availabilityId": availability_id},
             f"delete availability {availability_id}",
             confirm=f"Delete availability window {availability_id}?", yes=yes)


@appointment_app.command("list")
def appointment_list(
    from_: Optional[str] = typer.Option(None, "--from", help="On or after this date (YYYY-MM-DD)."),
    to: Optional[str] = typer.Option(None, "--to", help="On or before this date (YYYY-MM-DD)."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """List calendar appointments (calendarEventsByDateRange publication,
    collection `calendar-events`; the pub also carries the linked events)."""
    client = _client()
    try:
        items = client.read_pub("calendarEventsByDateRange", _range_params(from_, to),
                                collection="calendar-events")
    except Exception as e:
        typer.echo(f"Error fetching appointments: {e}", err=True)
        raise typer.Exit(1)
    finally:
        client.close()
    items = _asof_filter(items, "appointment")
    items.sort(key=lambda a: (a.get("start") or {}).get("$date", 0)
               if isinstance(a.get("start"), dict) else 0)
    if not output_json:
        for item in items:
            item["type"] = CALENDAR_EVENT_TYPE.get(item.get("type"), s(item.get("type")))
            item["start"] = _dt_str(item.get("start"))
            item["end"] = _dt_str(item.get("end"))
    _emit(items, output_json, columns=[
        ("Id", "_id"), ("Start", "start"), ("End", "end"), ("Type", "type"),
        ("Title", "title"), ("Event", "eventId"), ("Attended", "attended"),
    ], what="appointment")


@appointment_app.command("get")
def appointment_get(
    appointment_id: str = typer.Argument(..., help="Calendar-event document id."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Show one appointment (calendarEvent publication; it also carries the
    appointment's activity entries, not shown here)."""
    client = _client()
    try:
        docs = client.read_pub("calendarEvent", [appointment_id],
                               collection="calendar-events")
    except Exception as e:
        typer.echo(f"Error fetching appointment {appointment_id}: {e}", err=True)
        raise typer.Exit(1)
    finally:
        client.close()
    if not docs:
        typer.echo(f"Error: appointment {appointment_id} not found.", err=True)
        raise typer.Exit(1)
    docs = _asof_filter(docs, "appointment")
    if not docs:
        asof.refuse(f"appointment {appointment_id} was created after the cutoff")
    _emit_record(docs[0], output_json)


@appointment_app.command("create")
def appointment_create(
    venue: Optional[str] = typer.Option(None, "--venue", help="Venue id (venueId)."),
    type_: Optional[str] = typer.Option(
        None, "--type", help="CalendarEventType, name or number (e.g. InternalMeeting, 11)."),
    start: Optional[str] = typer.Option(
        None, "--start", help="Start: ISO datetime (naive = UTC)."),
    end: Optional[str] = typer.Option(
        None, "--end", help="End: ISO datetime, at least 15 minutes after start."),
    title: Optional[str] = typer.Option(None, "--title", help="Appointment title."),
    event: Optional[str] = typer.Option(None, "--event", help="Linked event id (eventId)."),
    data: Optional[str] = typer.Option(None, "--data", help="Full doc as JSON (flags overlay it)."),
    file: Optional[str] = typer.Option(None, "--file", "-f", help="Read the doc JSON from a file."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Create a calendar appointment (calendarEventCreate); prints the new id.

    The arg is the flat CalendarEventCreateSchema doc (docs/sonas.md §6.2):
    venueId, type, start required; end, title, staffId, eventId, allDay,
    weatherType, attendants optional. Create itself carries no notification
    field; reminder mails belong to the customer appointment types
    (docs/sonas.md §7), so an InternalMeeting with no event link is a plain
    staff-calendar entry.
    """
    doc = _read_data(data, file) if (data is not None or file is not None) else {}
    for key, value in (("venueId", venue), ("title", title), ("eventId", event)):
        if value is not None:
            doc[key] = value
    if type_ is not None:
        doc["type"] = _parse_enum(type_, CALENDAR_EVENT_TYPE, "appointment type")
    if start is not None:
        doc["start"] = _datetime_ejson(start)
    if end is not None:
        doc["end"] = _datetime_ejson(end)
    missing = [k for k in ("venueId", "type", "start") if doc.get(k) is None]
    if missing:
        typer.echo(f"Error: doc is missing required key(s): {', '.join(missing)}.", err=True)
        raise typer.Exit(1)
    _do_call("calendarEventCreate", doc, "create appointment", output_json=output_json)


@appointment_app.command("update")
def appointment_update(
    appointment_id: str = typer.Argument(..., help="Calendar-event document id."),
    data: Optional[str] = typer.Option(
        None, "--data",
        help="Mongo modifier JSON over CalendarEventSchema fields; $set must "
             "carry start and end together (docs/sonas.md §6.2)."),
    file: Optional[str] = typer.Option(None, "--file", "-f", help="Read the modifier JSON from a file."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Update an appointment (calendarEventUpdate); the $set needs start and
    end alongside any other change."""
    _do_call("calendarEventUpdate",
             {"id": appointment_id, "modifier": _read_data(data, file)},
             f"update appointment {appointment_id}", output_json=output_json)


@appointment_app.command("delete")
def appointment_delete(
    appointment_id: str = typer.Argument(..., help="Calendar-event document id."),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
):
    """Delete an appointment (calendarEventDelete)."""
    _do_call("calendarEventDelete", {"id": appointment_id},
             f"delete appointment {appointment_id}",
             confirm=f"Delete appointment {appointment_id}?", yes=yes)


@tasting_app.command("list")
def tasting_list(
    from_: Optional[str] = typer.Option(None, "--from", help="On or after this date (YYYY-MM-DD)."),
    to: Optional[str] = typer.Option(None, "--to", help="On or before this date (YYYY-MM-DD)."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """List tasting events (tastingEventsByDateRange publication, collection
    `tasting-events`)."""
    client = _client()
    try:
        items = client.read_pub("tastingEventsByDateRange", _range_params(from_, to),
                                collection="tasting-events")
    except Exception as e:
        typer.echo(f"Error fetching tasting events: {e}", err=True)
        raise typer.Exit(1)
    finally:
        client.close()
    items = _asof_filter(items, "tasting event")
    if not output_json:
        for item in items:
            item["startTime"] = _dt_str(item.get("startTime"))
    _emit(items, output_json, columns=[
        ("Id", "_id"), ("Start", "startTime"), ("Type", "type"),
        ("Capacity/slot", "capacityPerSlot"), ("Interval(min)", "timeInterval"),
        ("Staff only", "staffOnly"), ("Venue", "venueId"),
    ], what="tasting event")


@tasting_app.command("book")
def tasting_book(
    previous: Optional[str] = typer.Option(
        None, "--previous", help="Booking this one replaces (previousBookingId)."),
    data: Optional[str] = typer.Option(None, "--data", help="The booking as JSON."),
    file: Optional[str] = typer.Option(None, "--file", "-f", help="Read the booking JSON from a file."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Book a couple onto a tasting event (eventAddTastingBooking). The booking
    is TastingBookingNHSchema (docs/sonas.md §6.2): tastingEventId, tastingSlot,
    eventId, foodToTaste, numberAttending required. May mail the couple
    (unverified; see docs/sonas.md §6)."""
    arg = {"booking": _read_data(data, file)}
    if previous is not None:
        arg["previousBookingId"] = previous
    _do_call("eventAddTastingBooking", arg, "book tasting", output_json=output_json)


@tasting_app.command("cancel")
def tasting_cancel(
    booking_id: str = typer.Argument(..., help="Tasting-booking document id."),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
):
    """Cancel a tasting booking (eventCancelBooking, the tasting-booking cancel
    despite the event-sounding name; unverified; see docs/sonas.md §6)."""
    _do_call("eventCancelBooking", {"bookingId": booking_id},
             f"cancel tasting booking {booking_id}",
             confirm=f"Cancel tasting booking {booking_id}?", yes=yes)


# ----------------------------------------------------------------------
# T3 catalog (read-only): one factory-made sub-app per tenant catalog table
# ----------------------------------------------------------------------


def _make_catalog_app(label: str, help_text: str, table: str, data_pub: str,
                      collection: str, columns: List[Tuple[str, str]]) -> typer.Typer:
    """A read-only catalog sub-app: `list` via the tabular two-step and
    `get <id>` by selector. The per-table data pubs and collections are
    recorded in docs/sonas.md §6.4."""
    sub = typer.Typer(help=help_text)

    @sub.command("list", help=f"List {label}s ({table} table).")
    def _list(
        limit: int = typer.Option(50, "--limit", help="Rows to fetch."),
        search: str = typer.Option("", "--search", help="Search term (the table's searchable columns)."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
    ):
        _tabular_list(table, data_pub, columns, output_json, label,
                      limit=limit, search=search, collection=collection)

    @sub.command("get", help=f"Show one {label} ({table} row by id).")
    def _get(
        record_id: str = typer.Argument(..., help=f"{label} document id."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
    ):
        client = _client()
        try:
            rows, _info = client.read_tabular(table, data_pub=data_pub,
                                              selector={"_id": record_id},
                                              limit=2, collection=collection)
        except Exception as e:
            typer.echo(f"Error fetching {label} {record_id}: {e}", err=True)
            raise typer.Exit(1)
        finally:
            client.close()
        if not rows:
            typer.echo(f"Error: {label} {record_id} not found in {table}.", err=True)
            raise typer.Exit(1)
        _emit_record(asof.current_state(rows[0], f"this {label} record"), output_json)

    return sub


# (cli name, help, table, data pub, collection, list columns); every table
# here is served by aldeed:tabular's generic data pub (docs/sonas.md §6.4).
CATALOG = [
    ("supplier", "Supplier directory (external vendors).", "SuppliersList",
     "tabular_genericPub", "suppliers",
     [("Id", "_id"), ("Company", "contactData.companyName"),
      ("Email", "contactData.email"), ("Phone", "contactData.phone"),
      ("Description", "description")]),
    ("service", "Bookable services and their options.", "ServiceList",
     "tabular_genericPub", "services",
     [("Id", "_id"), ("Name", "name"), ("Options", "options"),
      ("Max options", "maxSelectedOptions"), ("Staff only", "staffOnly"),
      ("Deleted", "deleted")]),
    ("drinks-package", "Drinks catalog entries.", "DrinksList",
     "tabular_genericPub", "drinks",
     [("Id", "_id"), ("Name", "name"), ("Measure", "measure"),
      ("Price", "price"), ("Type", "type"), ("Description", "description")]),
    ("package", "Price-list packages.", "PackageList",
     "tabular_genericPub", "price-lists",
     [("Id", "_id"), ("Name", "name"), ("Type", "type"),
      ("Description", "descriptionText")]),
    ("template", "Venue templates of every kind: emails, documents, and the "
     "T&C/policy bodies a couple signs (the policy is a template, not a per-event "
     "record).", "TemplatesList",
     "tabular_genericPub", "templates",
     [("Id", "_id"), ("Name", "name"), ("Type", "type"),
      ("Subject", "subject"), ("Venue", "venueId")]),
    ("category", "Tag-partitioned option lists (enquiry source, heard-about-us, ...).",
     "CategoriesList", "tabular_genericPub", "categories",
     [("Id", "_id"), ("Name", "name"), ("Tag", "tag"), ("Slug", "slug"),
      ("Status", "status")]),
    ("venue", "The tenant's venues.", "VenueList",
     "tabular_genericPub", "venues",
     [("Id", "_id"), ("Name", "name"), ("Initials", "initials"),
      ("Capacity", "capacity"), ("Timezone", "timezone"), ("Website", "website")]),
    ("user", "Staff user accounts.", "UserList",
     "tabular_genericPub", "users",
     [("Id", "_id"), ("First", "profile.firstname"), ("Last", "profile.lastname"),
      ("Email", "emails.0.address")]),
]

_catalog_apps = {}
for _name, _help, _table, _pub, _coll, _cols in CATALOG:
    _capp = _make_catalog_app(_name.replace("-", " "), _help, _table, _pub,
                              _coll, _cols)
    _catalog_apps[_name] = _capp
    app.add_typer(_capp, name=_name)


@_catalog_apps["template"].command("edit")
def template_edit(
    template_id: str = typer.Argument(..., help="Template id (from `template list/get`)."),
    body_file: Optional[str] = typer.Option(
        None, "--body-file", help="Set the HTML body from a file (composed into $set.body)."),
    subject: Optional[str] = typer.Option(None, "--subject", help="Set the subject (<=255)."),
    name: Optional[str] = typer.Option(None, "--name", help="Set the template name."),
    data: Optional[str] = typer.Option(
        None, "--data",
        help='A raw Mongo modifier, e.g. {"$set": {"body": "<p>..</p>"}}. Overrides the '
             "--body-file/--subject/--name shortcuts."),
    file: Optional[str] = typer.Option(None, "--file", "-f", help="Read the modifier JSON from a file."),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Edit a template (templateUpdate). The modifier's keys are template schema
    fields (`body`, `subject`, `name`, `style`, `footerTemplateId`; docs/sonas.md
    §6).

    Example — update the T&C/policy a couple signs (it is a type-8 template):

        crude-sonas template list                 # find the type-8 "...Policy" row
        crude-sonas template get <id>              # read its current body
        crude-sonas template edit <id> --body-file policy.html

    `type` is a fixed Sonas enum (type 8 = T&C/policy, the same for every venue),
    shown in the `template list` Type column."""
    if data is not None or file is not None:
        modifier = _read_data(data, file)
    else:
        setter = {}
        if body_file is not None:
            with open(body_file) as f:
                setter["body"] = f.read()
        if subject is not None:
            setter["subject"] = subject
        if name is not None:
            setter["name"] = name
        if not setter:
            typer.echo("Error: provide --body-file/--subject/--name, or --data/-f.", err=True)
            raise typer.Exit(2)
        modifier = {"$set": setter}
    _do_call("templateUpdate", {"templateId": template_id, "modifier": modifier},
             f"edit template {template_id}",
             confirm=f"Edit template {template_id}?", yes=yes, output_json=output_json)

report_app = typer.Typer(help="Saved report definitions (sales funnel, revenue, marketing).")
app.add_typer(report_app, name="report")


@report_app.command("list")
def report_list(
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """List reports (reportsBasicInfo publication: id, name and type only)."""
    _pub_list("reportsBasicInfo", [], columns=[
        ("Id", "_id"), ("Name", "name"), ("Type", "type"),
    ], output_json=output_json, what="report", collection="reports", current=True)


@report_app.command("get")
def report_get(
    report_id: str = typer.Argument(..., help="Report document id (from `report list`)."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Show one report definition with its query lines (report publication)."""
    client = _client()
    try:
        docs = client.read_pub("report", [report_id], collection="reports")
    except Exception as e:
        typer.echo(f"Error fetching report {report_id}: {e}", err=True)
        raise typer.Exit(1)
    finally:
        client.close()
    if not docs:
        typer.echo(f"Error: report {report_id} not found.", err=True)
        raise typer.Exit(1)
    _emit_record(asof.current_state(docs[0], "this report definition"), output_json)


if __name__ == "__main__":
    app()
