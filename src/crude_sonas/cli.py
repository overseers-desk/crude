"""Typer CLI for Sonas wedding-venue software: crude-sonas.

Sonas has no public API; this drives its Meteor DDP backend directly (see
``crude_sonas.client`` and ``docs/sonas.md``). This minimal surface ships the
``event`` reads; the full resource map and the plan for the rest live in
``docs/sonas.md``.
"""

import json
from typing import Optional

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


def _render_record(item: dict) -> None:
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Field")
    table.add_column("Value")
    for key, value in item.items():
        if isinstance(value, dict) and "$date" in value:
            cell = date_str(value)
        elif isinstance(value, dict):
            cell = "(object)"
        elif isinstance(value, list):
            cell = f"{len(value)} item(s)"
        else:
            cell = _s(value)
        if len(cell) > 200:
            cell = cell[:197] + "..."
        table.add_row(key, cell)
    console.print(table)


def _status_matches(doc: dict, query: str) -> bool:
    status = doc.get("status")
    return str(status) == query or EVENT_STATUS.get(status, "").lower() == query.lower()


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
