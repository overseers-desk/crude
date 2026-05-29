"""Typer CLI for the Rezdy Supplier API: crude-rezdy."""

import json
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from crude_common.claude_command import register_claude_command
from crude_common.config import find_config as _find_config, read_config as _read_config, s as _s

app = typer.Typer(help="crude-rezdy — Rezdy Supplier API (products, availability, bookings).")
product_app = typer.Typer(help="Rezdy products.")
availability_app = typer.Typer(help="Rezdy availability (sessions).")
booking_app = typer.Typer(help="Rezdy bookings.")
app.add_typer(product_app, name="product")
app.add_typer(availability_app, name="availability")
app.add_typer(booking_app, name="booking")
console = Console()

register_claude_command(app, "crude-rezdy")


def _make_client(config: dict):
    from crude_rezdy.client import RezdyClient
    rezdy = config.get("rezdy", {})
    api_key = rezdy.get("api_key")
    if not api_key:
        typer.echo("Error: config.toml must contain [rezdy] api_key.", err=True)
        raise typer.Exit(1)
    environment = rezdy.get("environment", "production")
    return RezdyClient(api_key, environment=environment)


def _customer_name(booking: dict) -> str:
    customer = booking.get("customer") or {}
    name = " ".join(p for p in (customer.get("firstName"), customer.get("lastName")) if p)
    return name or _s(customer.get("email"))


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
    limit: int = typer.Option(20, "--limit", help="Maximum number of results."),
    offset: int = typer.Option(0, "--offset", help="Number of results to skip."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """List bookings.

    For a single day's bookings, set --from and --to to that day's start and
    end (e.g. --from 2026-05-25T00:00:00Z --to 2026-05-25T23:59:59Z).
    """
    config = _read_config(_find_config())
    client = _make_client(config)

    try:
        items = client.list_bookings(
            order_status=status,
            search=search,
            product_code=product,
            min_tour_start=from_,
            max_tour_start=to,
            min_date_created=created_from,
            max_date_created=created_to,
            limit=limit,
            offset=offset,
        )
    except Exception as e:
        typer.echo(f"Error fetching bookings: {e}", err=True)
        raise typer.Exit(1)

    if output_json:
        typer.echo(json.dumps(items, indent=2))
        return

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Order", style="dim")
    table.add_column("Status")
    table.add_column("Customer")
    table.add_column("Total", justify="right")
    table.add_column("Created")

    for item in items:
        total = f"{_s(item.get('totalAmount'))} {_s(item.get('totalCurrency'))}".strip()
        table.add_row(
            _s(item.get("orderNumber")),
            _s(item.get("status")),
            _customer_name(item),
            total,
            _s(item.get("dateCreated")),
        )

    console.print(table)
    typer.echo(f"\n{len(items)} booking(s) found.")


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
