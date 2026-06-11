"""Typer CLI for Sonas wedding-venue software: crude-sonas.

Sonas has no public API; this drives its Meteor DDP backend directly (see
``crude_sonas.client`` and ``docs/sonas.md``). This minimal surface ships the
``event`` reads; the full resource map and the plan for the rest live in
``docs/sonas.md``.
"""

import json
import sys
from typing import List, Optional, Tuple

import typer
from rich.console import Console
from rich.table import Table

from crude_common.claude_command import register_claude_command
from crude_common.config import (
    account as _account,
    find_config as _find_config,
    read_config as _read_config,
    resolve_account as _resolve_account,
    s as _s,
)
from crude_sonas.client import EVENT_STATUS, EVENT_TYPE, date_str

app = typer.Typer(help="crude-sonas — Sonas wedding-venue software (app.sonas.events).")
event_app = typer.Typer(help="Events (weddings and other bookings).")
app.add_typer(event_app, name="event")
console = Console()

register_claude_command(app)


def _make_client(config: dict):
    from crude_sonas.client import SonasClient, DEFAULT_FINGERPRINT
    sonas = _resolve_account(config, "sonas", _account())
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
    return _make_client(_read_config(_find_config()))


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
    for col in ("Ref", "Date", "Status", "Type", "Couple", "Guests", "Name"):
        table.add_column(col, style="dim" if col == "Ref" else None)
    for ev in events:
        table.add_row(
            _s(ev.get("reference")),
            date_str(ev.get("date")),
            EVENT_STATUS.get(ev.get("status"), _s(ev.get("status"))),
            EVENT_TYPE.get(ev.get("type"), _s(ev.get("type"))),
            _couple(ev),
            _guests(ev),
            _s(ev.get("name")),
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
        cell = _s(value)
    if len(cell) > max_len:
        cell = cell[: max_len - 3] + "..."
    return cell


def _render_record(item: dict) -> None:
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Field")
    table.add_column("Value")
    for key, value in item.items():
        table.add_row(key, _cell(value, 200))
    console.print(table)


def _emit(items: list, output_json: bool,
          columns: Optional[List[Tuple[str, str]]] = None, what: str = "row") -> None:
    """Render a list result: raw JSON, or a Rich table plus a count line.

    ``columns`` is (header, key) pairs; None derives headers from the union of
    the items' keys (minus ``_collection``), capped at 8 to stay readable.
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
            table.add_row(*(_cell(item.get(key)) for _header, key in columns))
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


def _pub_list(pub: str, params: list, columns: Optional[List[Tuple[str, str]]],
              output_json: bool, what: str, collection: Optional[str] = None) -> None:
    """List command body: subscribe to a publication and emit its documents."""
    client = _client()
    try:
        items = client.read_pub(pub, params, collection=collection)
    except Exception as e:
        typer.echo(f"Error fetching {what}s: {e}", err=True)
        raise typer.Exit(1)
    finally:
        client.close()
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


def _tabular_list(table: str, data_pub: str, columns: Optional[List[Tuple[str, str]]],
                  output_json: bool, what: str, limit: int = 50, search: str = "",
                  selector: Optional[dict] = None) -> None:
    """List command body for aldeed:tabular tables (the two-step in docs/sonas.md §5)."""
    client = _client()
    try:
        rows, info = client.read_tabular(
            table, data_pub=data_pub, selector=selector, limit=limit, search=search
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
    _emit(rows, output_json, columns=columns, what=what)
    if not output_json and info.get("recordsFiltered") is not None \
            and info.get("recordsTotal") is not None:
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
    client = _make_client(_read_config(_find_config()))
    try:
        events = client.list_events(from_, to)
    except Exception as e:
        typer.echo(f"Error listing events: {e}", err=True)
        raise typer.Exit(1)
    finally:
        client.close()
    if status:
        events = [e for e in events if _status_matches(e, status)]
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
    client = _make_client(_read_config(_find_config()))
    try:
        event = client.get_event(event_id)
    except Exception as e:
        typer.echo(f"Error fetching event {event_id}: {e}", err=True)
        raise typer.Exit(1)
    finally:
        client.close()
    if output_json:
        typer.echo(json.dumps(event, indent=2))
        return
    _render_record(event)


if __name__ == "__main__":
    app()
