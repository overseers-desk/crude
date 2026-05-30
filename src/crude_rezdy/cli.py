"""Typer CLI for the Rezdy Supplier API: crude-rezdy."""

import json
from datetime import datetime, time, timezone as _utc
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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

app = typer.Typer(help="crude-rezdy — Rezdy Supplier API (products, availability, bookings).")
product_app = typer.Typer(help="Rezdy products.")
availability_app = typer.Typer(help="Rezdy availability (sessions).")
booking_app = typer.Typer(help="Rezdy bookings.")
app.add_typer(product_app, name="product")
app.add_typer(availability_app, name="availability")
app.add_typer(booking_app, name="booking")
console = Console()

register_claude_command(app)


def _parse_timezone(rezdy: dict) -> ZoneInfo:
    """Return the account's timezone as a ZoneInfo, erroring if unset or unknown.

    A timezone is a required field on a rezdy account: every date a user types is
    meant as that account's operational day, so crude needs the zone to read it
    correctly. The requirement is enforced wherever a rezdy client is built, not
    deferred to a particular filter, since date-bearing queries are plentiful and
    a missing zone is a config error regardless of which one is run.
    """
    name = rezdy.get("timezone")
    if not name:
        which = f"[rezdy.{_account()}]" if _account() else "[rezdy]"
        typer.echo(
            f"Error: {which} must set a timezone (IANA name, e.g. "
            f"\"Australia/Brisbane\"); rezdy reads typed dates as the account's "
            f"operational day.",
            err=True,
        )
        raise typer.Exit(1)
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        typer.echo(f"Error: unknown timezone '{name}' in rezdy config.", err=True)
        raise typer.Exit(1)


def _make_client(config: dict):
    from crude_rezdy.client import RezdyClient
    rezdy = _resolve_account(config, "rezdy", _account())
    api_key = rezdy.get("api_key")
    if not api_key:
        typer.echo("Error: config.toml must contain [rezdy] api_key.", err=True)
        raise typer.Exit(1)
    _parse_timezone(rezdy)  # required field; fail early if missing or unknown
    environment = rezdy.get("environment", "production")
    return RezdyClient(api_key, environment=environment)


def _account_timezone(config: dict) -> ZoneInfo:
    """The selected rezdy account's timezone, for the client-side instant filters."""
    return _parse_timezone(_resolve_account(config, "rezdy", _account()))


def _day_bound_utc(date_str: str, tz: ZoneInfo, *, end: bool) -> str:
    """Map a typed --from/--to into a UTC instant for comparison with dateUpdated.

    A bare YYYY-MM-DD is read as the start (or, for --to, the end) of that day in
    the account's zone, then converted to UTC and rendered as a ...Z string, so a
    lexicographic compare against Rezdy's ...Z dateUpdated is correct. A value that
    already carries a time is passed through unchanged.
    """
    if date_str and len(date_str) == 10:
        y, m, d = (int(p) for p in date_str.split("-"))
        bound = time(23, 59, 59) if end else time(0, 0, 0)
        local = datetime(y, m, d, bound.hour, bound.minute, bound.second, tzinfo=tz)
        return local.astimezone(_utc.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return date_str


def _customer_name(booking: dict) -> str:
    customer = booking.get("customer") or {}
    name = " ".join(p for p in (customer.get("firstName"), customer.get("lastName")) if p)
    return name or _s(customer.get("email"))


def _refund_count(booking: dict) -> int:
    return sum(
        1 for p in booking.get("payments", [])
        if p.get("amount", 0) < 0 or "REFUND" in p.get("type", "").upper()
    )


def _first_item(booking: dict) -> dict:
    items = booking.get("items") or []
    return items[0] if items else {}


def _render_record(item: dict) -> None:
    """Print a record's scalar top-level fields as a Field/Value table.

    Nested objects and lists are summarised rather than expanded; use --json
    for the full structure.
    """
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Field")
    table.add_column("Value")
    for key, value in item.items():
        if isinstance(value, dict):
            value_str = "(object)"
        elif isinstance(value, list):
            value_str = f"{len(value)} item(s)"
        else:
            value_str = _s(value)
        if len(value_str) > 200:
            value_str = value_str[:197] + "..."
        table.add_row(key, value_str)
    console.print(table)


@product_app.command("list")
def list_products(
    search: Optional[str] = typer.Option(None, "--search", help="Filter by name, product code, or internal code."),
    limit: int = typer.Option(20, "--limit", help="Maximum number of results."),
    offset: int = typer.Option(0, "--offset", help="Number of results to skip."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """List products."""
    config = _read_config(_find_config())
    client = _make_client(config)

    try:
        items = client.list_products(search=search, limit=limit, offset=offset)
    except Exception as e:
        typer.echo(f"Error fetching products: {e}", err=True)
        raise typer.Exit(1)

    if output_json:
        typer.echo(json.dumps(items, indent=2))
        return

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Code", style="dim")
    table.add_column("Name")
    table.add_column("Type")
    table.add_column("Price", justify="right")

    for item in items:
        table.add_row(
            _s(item.get("productCode")),
            _s(item.get("name")),
            _s(item.get("productType")),
            _s(item.get("advertisedPrice")),
        )

    console.print(table)
    typer.echo(f"\n{len(items)} product(s) found.")


@product_app.command("get")
def get_product(
    product_code: str = typer.Argument(..., help="Product code (e.g. 'P12345')."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """Show a single product."""
    config = _read_config(_find_config())
    client = _make_client(config)

    try:
        item = client.get_product(product_code)
    except Exception as e:
        typer.echo(f"Error fetching product {product_code}: {e}", err=True)
        raise typer.Exit(1)

    if output_json:
        typer.echo(json.dumps(item, indent=2))
        return
    _render_record(item)


@availability_app.command("list")
def list_availability(
    product: str = typer.Option(..., "--product", help="Product code to check availability for."),
    from_: str = typer.Option(..., "--from", help="Start of range, local time 'YYYY-MM-DD HH:mm:ss'."),
    to: str = typer.Option(..., "--to", help="End of range, local time 'YYYY-MM-DD HH:mm:ss'."),
    min_availability: Optional[int] = typer.Option(None, "--min-availability", help="Only sessions with at least this many seats."),
    limit: int = typer.Option(100, "--limit", help="Maximum number of results."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """List availability sessions for a product within a date range."""
    config = _read_config(_find_config())
    client = _make_client(config)

    try:
        items = client.list_availability(
            product, from_, to, min_availability=min_availability, limit=limit
        )
    except Exception as e:
        typer.echo(f"Error fetching availability: {e}", err=True)
        raise typer.Exit(1)

    if output_json:
        typer.echo(json.dumps(items, indent=2))
        return

    table = Table(show_header=True, header_style="bold green")
    table.add_column("Session ID", style="dim")
    table.add_column("Start")
    table.add_column("End")
    table.add_column("Seats", justify="right")

    for item in items:
        table.add_row(
            _s(item.get("id")),
            _s(item.get("startTimeLocal")),
            _s(item.get("endTimeLocal")),
            _s(item.get("seatsAvailable")),
        )

    console.print(table)
    typer.echo(f"\n{len(items)} session(s) found.")


@booking_app.command("list")
def list_bookings(
    status: Optional[str] = typer.Option(None, "--status", help="Filter by order status (e.g. CONFIRMED, CANCELLED)."),
    search: Optional[str] = typer.Option(None, "--search", help="Search order number, customer name, agent or voucher code."),
    product: Optional[str] = typer.Option(None, "--product", help="Filter by product code."),
    from_: Optional[str] = typer.Option(None, "--from", help="Tour starts on or after this time (ISO 8601)."),
    to: Optional[str] = typer.Option(None, "--to", help="Tour starts before or on this time (ISO 8601)."),
    created_from: Optional[str] = typer.Option(None, "--created-from", help="Created on or after this date (ISO 8601)."),
    created_to: Optional[str] = typer.Option(None, "--created-to", help="Created on or before this date (ISO 8601)."),
    updated_from: Optional[str] = typer.Option(None, "--updated-from", help="Last updated on or after this date (YYYY-MM-DD or ISO 8601, client-side filter)."),
    updated_to: Optional[str] = typer.Option(None, "--updated-to", help="Last updated on or before this date (YYYY-MM-DD or ISO 8601, client-side filter)."),
    limit: int = typer.Option(20, "--limit", help="Maximum number of results."),
    offset: int = typer.Option(0, "--offset", help="Number of results to skip."),
    fetch_all: bool = typer.Option(False, "--all", help="Fetch all pages automatically (ignores --limit and --offset)."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """List bookings.

    For a single day's bookings, set --from and --to to that day's start and
    end (e.g. --from 2026-05-25T00:00:00Z --to 2026-05-25T23:59:59Z).
    Use --updated-from / --updated-to to filter by when the booking was last
    modified (e.g. when it was cancelled).
    """
    config = _read_config(_find_config())
    client = _make_client(config)

    kwargs = dict(
        order_status=status,
        search=search,
        product_code=product,
        min_tour_start=from_,
        max_tour_start=to,
        min_date_created=created_from,
        max_date_created=created_to,
    )

    try:
        if fetch_all:
            items = client.paginate(limit=100, **kwargs)
        else:
            items = client.list_bookings(limit=limit, offset=offset, **kwargs)
    except Exception as e:
        typer.echo(f"Error fetching bookings: {e}", err=True)
        raise typer.Exit(1)

    if updated_from or updated_to:
        tz = _account_timezone(config)
        if updated_from:
            lo = _day_bound_utc(updated_from, tz, end=False)
            items = [b for b in items if b.get("dateUpdated", "") >= lo]
        if updated_to:
            hi = _day_bound_utc(updated_to, tz, end=True)
            items = [b for b in items if b.get("dateUpdated", "") <= hi]

    if output_json:
        typer.echo(json.dumps(items, indent=2))
        return

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Order", style="dim")
    table.add_column("Status")
    table.add_column("Customer")
    table.add_column("Product")
    table.add_column("Session")
    table.add_column("Paid/Total", justify="right")
    table.add_column("Updated")

    for item in items:
        first = _first_item(item)
        paid_total = f"{_s(item.get('totalPaid'))}/{_s(item.get('totalAmount'))} {_s(item.get('totalCurrency'))}".strip()
        table.add_row(
            _s(item.get("orderNumber")),
            _s(item.get("status")),
            _customer_name(item),
            _s(first.get("productName", ""))[:35],
            _s(first.get("startTimeLocal", ""))[:16],
            paid_total,
            _s(item.get("dateUpdated", ""))[:10],
        )

    console.print(table)
    typer.echo(f"\n{len(items)} booking(s) found.")


@booking_app.command("cancellations")
def list_cancellations(
    from_: Optional[str] = typer.Option(None, "--from", help="Cancellations on or after this date (YYYY-MM-DD)."),
    to: Optional[str] = typer.Option(None, "--to", help="Cancellations on or before this date (YYYY-MM-DD)."),
    limit: int = typer.Option(100, "--limit", help="Maximum number of results (ignored when --all is set)."),
    fetch_all: bool = typer.Option(False, "--all", help="Fetch all pages automatically."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """List cancelled bookings, filtered by when the cancellation occurred.

    --from and --to filter against the cancellation date (dateUpdated), not the
    session date. Use --all to ensure no results are missed.
    """
    config = _read_config(_find_config())
    client = _make_client(config)

    try:
        if fetch_all:
            items = client.paginate(limit=100, order_status="CANCELLED")
        else:
            items = client.list_bookings(order_status="CANCELLED", limit=limit)
    except Exception as e:
        typer.echo(f"Error fetching cancellations: {e}", err=True)
        raise typer.Exit(1)

    if from_ or to:
        tz = _account_timezone(config)
        if from_:
            lo = _day_bound_utc(from_, tz, end=False)
            items = [b for b in items if b.get("dateUpdated", "") >= lo]
        if to:
            hi = _day_bound_utc(to, tz, end=True)
            items = [b for b in items if b.get("dateUpdated", "") <= hi]

    if output_json:
        typer.echo(json.dumps(items, indent=2))
        return

    table = Table(show_header=True, header_style="bold red")
    table.add_column("Order", style="dim")
    table.add_column("Customer")
    table.add_column("Product")
    table.add_column("Session")
    table.add_column("Cancelled On")
    table.add_column("Paid/Total", justify="right")
    table.add_column("Refunds", justify="right")
    table.add_column("Notes")

    for item in items:
        first = _first_item(item)
        paid_total = f"{_s(item.get('totalPaid'))}/{_s(item.get('totalAmount'))} {_s(item.get('totalCurrency'))}".strip()
        notes = (item.get("internalNotes") or "").strip()
        table.add_row(
            _s(item.get("orderNumber")),
            _customer_name(item),
            _s(first.get("productName", ""))[:35],
            _s(first.get("startTimeLocal", ""))[:16],
            _s(item.get("dateUpdated", ""))[:10],
            paid_total,
            str(_refund_count(item)),
            notes[:60],
        )

    console.print(table)
    typer.echo(f"\n{len(items)} cancellation(s) found.")


@booking_app.command("get")
def get_booking(
    order_number: str = typer.Argument(..., help="Order number (e.g. 'R123456')."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """Show a single booking."""
    config = _read_config(_find_config())
    client = _make_client(config)

    try:
        item = client.get_booking(order_number)
    except Exception as e:
        typer.echo(f"Error fetching booking {order_number}: {e}", err=True)
        raise typer.Exit(1)

    if output_json:
        typer.echo(json.dumps(item, indent=2))
        return
    _render_record(item)


if __name__ == "__main__":
    app()
