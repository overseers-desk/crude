"""Typer CLI for the Rezdy Supplier API: crude-rezdy."""

import json
import sys
from datetime import datetime, time, timezone as _utc
from typing import Callable, Optional
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
from crude_common.output import emit_list, emit_record
from crude_common.writeio import do_write, merge_update, read_data

app = typer.Typer(help="crude-rezdy — Rezdy Supplier API (products, availability, bookings, and more).")
product_app = typer.Typer(help="Rezdy products.")
availability_app = typer.Typer(help="Rezdy availability (sessions).")
booking_app = typer.Typer(help="Rezdy bookings.")
customer_app = typer.Typer(help="Rezdy customers.")
extra_app = typer.Typer(help="Rezdy extras (add-ons).")
pickup_app = typer.Typer(help="Rezdy pickup lists.")
category_app = typer.Typer(help="Rezdy categories (and their product membership).")
rate_app = typer.Typer(help="Rezdy rates (and their product membership).")
resource_app = typer.Typer(help="Rezdy resources (and their session assignment).")
manifest_app = typer.Typer(help="Rezdy manifest / check-in.")
voucher_app = typer.Typer(help="Rezdy vouchers (read-only).")
company_app = typer.Typer(help="Rezdy company (read-only).")
app.add_typer(product_app, name="product")
app.add_typer(availability_app, name="availability")
app.add_typer(booking_app, name="booking")
app.add_typer(customer_app, name="customer")
app.add_typer(extra_app, name="extra")
app.add_typer(pickup_app, name="pickup-list")
app.add_typer(category_app, name="category")
app.add_typer(rate_app, name="rate")
app.add_typer(resource_app, name="resource")
app.add_typer(manifest_app, name="manifest")
app.add_typer(voucher_app, name="voucher")
app.add_typer(company_app, name="company")
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


def _client():
    """The configured rezdy client for the selected account."""
    return _make_client(_read_config(_find_config()))


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


# ----------------------------------------------------------------------
# Booking-display helpers (for the bespoke booking/cancellation tables)
# ----------------------------------------------------------------------


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


# ----------------------------------------------------------------------
# Products
# ----------------------------------------------------------------------


@product_app.command("list")
def list_products(
    search: Optional[str] = typer.Option(None, "--search", help="Filter by name, product code, or internal code."),
    limit: int = typer.Option(20, "--limit", help="Maximum number of results."),
    offset: int = typer.Option(0, "--offset", help="Number of results to skip."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """List products."""
    client = _client()
    try:
        items = client.list_products(search=search, limit=limit, offset=offset)
    except Exception as e:
        typer.echo(f"Error fetching products: {e}", err=True)
        raise typer.Exit(1)
    emit_list(items, [
        ("Code", "productCode"),
        ("Name", "name"),
        ("Type", "productType"),
        ("Price", "advertisedPrice"),
    ], "product", output_json)


@product_app.command("get")
def get_product(
    product_code: str = typer.Argument(..., help="Product code (e.g. 'P12345')."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """Show a single product."""
    client = _client()
    try:
        item = client.get_product(product_code)
    except Exception as e:
        typer.echo(f"Error fetching product {product_code}: {e}", err=True)
        raise typer.Exit(1)
    emit_record(item, output_json)


@product_app.command("create")
def create_product(
    data: Optional[str] = typer.Option(None, "--data", help="Product object as JSON (or -f / stdin)."),
    file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
):
    """Create a product from a JSON body."""
    client = _client()
    body = read_data(data, file)
    do_write(lambda: client.create_product(body), "create product",
              confirm="Create this product?", yes=yes, output_json=output_json)


@product_app.command("update")
def update_product(
    product_code: str = typer.Argument(..., help="Product code to update."),
    name: Optional[str] = typer.Option(None, "--name", help="Set the product name."),
    terms: Optional[str] = typer.Option(None, "--terms", help="Set the product's terms & conditions."),
    data: Optional[str] = typer.Option(None, "--data", help="Partial JSON overlaying the fetched product."),
    file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON overlay from a file."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
):
    """Update a product (read-merge-write).

    The current product is fetched, the supplied flags and --data overlay it, and
    the merged object is written back, so a single field (e.g. --terms) changes
    without dropping the rest.
    """
    client = _client()
    flags = {"name": name, "terms": terms}
    merge_update(
        lambda: client.get_product(product_code),
        lambda merged: client.update_product(product_code, merged),
        data, file, flags, f"update product {product_code}", yes, output_json,
    )


@product_app.command("delete")
def delete_product(
    product_code: str = typer.Argument(..., help="Product code to delete."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
):
    """Delete a product."""
    client = _client()
    do_write(lambda: client.delete_product(product_code), f"delete product {product_code}",
              confirm=f"Delete product {product_code}?", yes=yes, output_json=output_json)


@product_app.command("image-add")
def add_product_image(
    product_code: str = typer.Argument(..., help="Product code."),
    data: Optional[str] = typer.Option(None, "--data", help="Image object as JSON (or -f / stdin)."),
    file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
):
    """Add an image to a product."""
    client = _client()
    body = read_data(data, file)
    do_write(lambda: client.add_product_image(product_code, body),
              f"add image to {product_code}", yes=yes, output_json=output_json)


@product_app.command("image-remove")
def remove_product_image(
    product_code: str = typer.Argument(..., help="Product code."),
    image_id: str = typer.Argument(..., help="Image id to remove."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
):
    """Remove an image from a product."""
    client = _client()
    do_write(lambda: client.delete_product_image(product_code, image_id),
              f"remove image {image_id} from {product_code}",
              confirm=f"Remove image {image_id} from {product_code}?", yes=yes, output_json=output_json)


@product_app.command("pickups")
def product_pickups(
    product_code: str = typer.Argument(..., help="Product code."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """List a product's pickup locations."""
    client = _client()
    try:
        items = client.get_product_pickups(product_code)
    except Exception as e:
        typer.echo(f"Error fetching pickups: {e}", err=True)
        raise typer.Exit(1)
    emit_list(items, [
        ("Name", "locationName"),
        ("Address", "address"),
        ("Pickup time", "pickupTime"),
    ], "pickup", output_json)




# ----------------------------------------------------------------------
# Availability (sessions)
# ----------------------------------------------------------------------


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
    client = _client()
    try:
        items = client.list_availability(
            product, from_, to, min_availability=min_availability, limit=limit
        )
    except Exception as e:
        typer.echo(f"Error fetching availability: {e}", err=True)
        raise typer.Exit(1)
    if not output_json:
        # Confirm which product the code names, resolving best-effort.
        try:
            label = client.product_names().get(product)
        except Exception:
            label = None
        typer.echo(f"Product: {label} ({product})" if label else f"Product: {product}")
    emit_list(items, [
        ("Session ID", "id"),
        ("Start", "startTimeLocal"),
        ("End", "endTimeLocal"),
        ("Seats", "seatsAvailable"),
    ], "session", output_json, header_style="bold green")


@availability_app.command("create")
def create_availability(
    data: Optional[str] = typer.Option(None, "--data", help="Session object as JSON (or -f / stdin)."),
    file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
):
    """Create an availability session from a JSON body."""
    client = _client()
    body = read_data(data, file)
    do_write(lambda: client.create_availability(body), "create session",
              confirm="Create this session?", yes=yes, output_json=output_json)


@availability_app.command("update")
def update_availability(
    product: str = typer.Option(..., "--product", help="Product code the session belongs to."),
    start_local: str = typer.Option(..., "--start-local", help="Session start, local time 'YYYY-MM-DD HH:mm:ss'."),
    data: Optional[str] = typer.Option(None, "--data", help="Full session object as JSON (or -f / stdin)."),
    file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
):
    """Update an availability session (keyed by product and local start time)."""
    client = _client()
    body = read_data(data, file)
    do_write(lambda: client.update_availability(product, start_local, body),
              f"update session {product} @ {start_local}",
              confirm=f"Update session {product} @ {start_local}?",
              yes=yes, output_json=output_json)


@availability_app.command("delete")
def delete_availability(
    product: str = typer.Option(..., "--product", help="Product code the session belongs to."),
    start_local: str = typer.Option(..., "--start-local", help="Session start, local time 'YYYY-MM-DD HH:mm:ss'."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
):
    """Delete an availability session (keyed by product and local start time)."""
    client = _client()
    do_write(lambda: client.delete_availability(product, start_local),
              f"delete session {product} @ {start_local}",
              confirm=f"Delete session {product} @ {start_local}?", yes=yes, output_json=output_json)


@availability_app.command("batch")
def batch_availability(
    data: Optional[str] = typer.Option(None, "--data", help="Batch object as JSON (or -f / stdin)."),
    file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
):
    """Batch-update availability from a JSON body."""
    client = _client()
    body = read_data(data, file)
    do_write(lambda: client.batch_availability(body), "batch availability update",
              confirm="Apply this batch availability update?", yes=yes, output_json=output_json)


# ----------------------------------------------------------------------
# Bookings
# ----------------------------------------------------------------------


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
    client = _client()
    try:
        item = client.get_booking(order_number)
    except Exception as e:
        typer.echo(f"Error fetching booking {order_number}: {e}", err=True)
        raise typer.Exit(1)
    emit_record(item, output_json)


@booking_app.command("quote")
def quote_booking(
    data: Optional[str] = typer.Option(None, "--data", help="Booking object as JSON (or -f / stdin)."),
    file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the quote."),
):
    """Quote a booking's price without creating or charging it."""
    client = _client()
    body = read_data(data, file)
    try:
        result = client.quote_booking(body)
    except Exception as e:
        typer.echo(f"Error quoting booking: {e}", err=True)
        raise typer.Exit(1)
    typer.echo(json.dumps(result, indent=2, default=str))


@booking_app.command("create")
def create_booking(
    data: Optional[str] = typer.Option(None, "--data", help="Booking object as JSON (or -f / stdin)."),
    file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
    notify: bool = typer.Option(False, "--notify", help="Let Rezdy send customer/supplier emails (default off)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
):
    """Create a booking from a JSON body.

    sendNotifications defaults off so a test booking does not email anyone; pass
    --notify to enable. The flag is authoritative over any sendNotifications in
    --data.
    """
    client = _client()
    body = read_data(data, file)
    body["sendNotifications"] = notify
    do_write(lambda: client.create_booking(body), "create booking",
              confirm="Create this booking? (a real order)", yes=yes, output_json=output_json)


@booking_app.command("update")
def update_booking(
    order_number: str = typer.Argument(..., help="Order number to update."),
    data: Optional[str] = typer.Option(None, "--data", help="Partial JSON (the API accepts status, customer, participants)."),
    file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
):
    """Update a booking (status, customer, or participants)."""
    client = _client()
    body = read_data(data, file)
    do_write(lambda: client.update_booking(order_number, body),
              f"update booking {order_number}", confirm=f"Update booking {order_number}?",
              yes=yes, output_json=output_json)


@booking_app.command("cancel")
def cancel_booking(
    order_number: str = typer.Argument(..., help="Order number to cancel."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
):
    """Cancel a booking."""
    client = _client()
    do_write(lambda: client.cancel_booking(order_number), f"cancel booking {order_number}",
              confirm=f"Cancel booking {order_number}?", yes=yes, output_json=output_json)


# ----------------------------------------------------------------------
# Customers
# ----------------------------------------------------------------------


@customer_app.command("list")
def list_customers(
    search: Optional[str] = typer.Option(None, "--search", help="Filter by name or email."),
    limit: int = typer.Option(20, "--limit", help="Maximum number of results."),
    offset: int = typer.Option(0, "--offset", help="Number of results to skip."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """List customers."""
    client = _client()
    try:
        items = client.list_customers(search=search, limit=limit, offset=offset)
    except Exception as e:
        typer.echo(f"Error fetching customers: {e}", err=True)
        raise typer.Exit(1)
    emit_list(items, [
        ("ID", "id"),
        ("Name", lambda c: " ".join(p for p in (c.get("firstName"), c.get("lastName")) if p)),
        ("Email", "email"),
        ("Phone", "phone"),
    ], "customer", output_json)


@customer_app.command("get")
def get_customer(
    customer_id: str = typer.Argument(..., help="Customer id."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """Show a single customer."""
    client = _client()
    try:
        item = client.get_customer(customer_id)
    except Exception as e:
        typer.echo(f"Error fetching customer {customer_id}: {e}", err=True)
        raise typer.Exit(1)
    emit_record(item, output_json)


@customer_app.command("create")
def create_customer(
    data: Optional[str] = typer.Option(None, "--data", help="Customer object as JSON (or -f / stdin)."),
    file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
):
    """Create a customer from a JSON body."""
    client = _client()
    body = read_data(data, file)
    do_write(lambda: client.create_customer(body), "create customer",
              confirm="Create this customer?", yes=yes, output_json=output_json)


@customer_app.command("delete")
def delete_customer(
    customer_id: str = typer.Argument(..., help="Customer id to delete."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
):
    """Delete a customer."""
    client = _client()
    do_write(lambda: client.delete_customer(customer_id), f"delete customer {customer_id}",
              confirm=f"Delete customer {customer_id}?", yes=yes, output_json=output_json)


# ----------------------------------------------------------------------
# Extras
# ----------------------------------------------------------------------


@extra_app.command("list")
def list_extras(
    search: Optional[str] = typer.Option(None, "--search", help="Filter by extra name; omit for all."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """List extras (add-ons)."""
    client = _client()
    try:
        items = client.list_extras(search=search or "")
    except Exception as e:
        typer.echo(f"Error fetching extras: {e}", err=True)
        raise typer.Exit(1)
    emit_list(items, [
        ("ID", "id"),
        ("Name", "name"),
        ("Price", "price"),
        ("Type", "extraPriceType"),
    ], "extra", output_json)


@extra_app.command("get")
def get_extra(
    extra_id: str = typer.Argument(..., help="Extra id."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """Show a single extra."""
    client = _client()
    try:
        item = client.get_extra(extra_id)
    except Exception as e:
        typer.echo(f"Error fetching extra {extra_id}: {e}", err=True)
        raise typer.Exit(1)
    emit_record(item, output_json)


@extra_app.command("create")
def create_extra(
    data: Optional[str] = typer.Option(None, "--data", help="Extra object as JSON (or -f / stdin)."),
    file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
):
    """Create an extra from a JSON body."""
    client = _client()
    body = read_data(data, file)
    do_write(lambda: client.create_extra(body), "create extra",
              confirm="Create this extra?", yes=yes, output_json=output_json)


@extra_app.command("update")
def update_extra(
    extra_id: str = typer.Argument(..., help="Extra id to update."),
    name: Optional[str] = typer.Option(None, "--name", help="Set the extra name."),
    data: Optional[str] = typer.Option(None, "--data", help="Partial JSON overlaying the fetched extra."),
    file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON overlay from a file."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
):
    """Update an extra (read-merge-write)."""
    client = _client()
    merge_update(
        lambda: client.get_extra(extra_id),
        lambda merged: client.update_extra(extra_id, merged),
        data, file, {"name": name}, f"update extra {extra_id}", yes, output_json,
    )


@extra_app.command("delete")
def delete_extra(
    extra_id: str = typer.Argument(..., help="Extra id to delete."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
):
    """Delete an extra."""
    client = _client()
    do_write(lambda: client.delete_extra(extra_id), f"delete extra {extra_id}",
              confirm=f"Delete extra {extra_id}?", yes=yes, output_json=output_json)


# ----------------------------------------------------------------------
# Pickup lists
# ----------------------------------------------------------------------


@pickup_app.command("list")
def list_pickup_lists(
    search: Optional[str] = typer.Option(None, "--search", help="Filter by pickup-list name; omit for all."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """List pickup lists."""
    client = _client()
    try:
        items = client.list_pickup_lists(search=search or "")
    except Exception as e:
        typer.echo(f"Error fetching pickup lists: {e}", err=True)
        raise typer.Exit(1)
    emit_list(items, [
        ("ID", "id"),
        ("Name", "name"),
        ("Locations", lambda p: len(p.get("pickupLocations") or [])),
    ], "pickup list", output_json)


@pickup_app.command("get")
def get_pickup_list(
    pickup_list_id: str = typer.Argument(..., help="Pickup list id."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """Show a single pickup list."""
    client = _client()
    try:
        item = client.get_pickup_list(pickup_list_id)
    except Exception as e:
        typer.echo(f"Error fetching pickup list {pickup_list_id}: {e}", err=True)
        raise typer.Exit(1)
    emit_record(item, output_json)


@pickup_app.command("create")
def create_pickup_list(
    data: Optional[str] = typer.Option(None, "--data", help="Pickup list object as JSON (or -f / stdin)."),
    file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
):
    """Create a pickup list from a JSON body."""
    client = _client()
    body = read_data(data, file)
    do_write(lambda: client.create_pickup_list(body), "create pickup list",
              confirm="Create this pickup list?", yes=yes, output_json=output_json)


@pickup_app.command("update")
def update_pickup_list(
    pickup_list_id: str = typer.Argument(..., help="Pickup list id to update."),
    name: Optional[str] = typer.Option(None, "--name", help="Set the pickup list name."),
    data: Optional[str] = typer.Option(None, "--data", help="Partial JSON overlaying the fetched pickup list."),
    file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON overlay from a file."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
):
    """Update a pickup list (read-merge-write)."""
    client = _client()
    merge_update(
        lambda: client.get_pickup_list(pickup_list_id),
        lambda merged: client.update_pickup_list(pickup_list_id, merged),
        data, file, {"name": name}, f"update pickup list {pickup_list_id}", yes, output_json,
    )


@pickup_app.command("delete")
def delete_pickup_list(
    pickup_list_id: str = typer.Argument(..., help="Pickup list id to delete."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
):
    """Delete a pickup list."""
    client = _client()
    do_write(lambda: client.delete_pickup_list(pickup_list_id),
              f"delete pickup list {pickup_list_id}",
              confirm=f"Delete pickup list {pickup_list_id}?", yes=yes, output_json=output_json)


# ----------------------------------------------------------------------
# Categories
# ----------------------------------------------------------------------


@category_app.command("list")
def list_categories(
    limit: int = typer.Option(100, "--limit", help="Maximum number of results."),
    offset: int = typer.Option(0, "--offset", help="Number of results to skip."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """List categories."""
    client = _client()
    try:
        items = client.list_categories(limit=limit, offset=offset)
    except Exception as e:
        typer.echo(f"Error fetching categories: {e}", err=True)
        raise typer.Exit(1)
    emit_list(items, [
        ("ID", "id"),
        ("Name", "name"),
        ("Visible", "visible"),
    ], "category", output_json)


@category_app.command("get")
def get_category(
    category_id: str = typer.Argument(..., help="Category id."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """Show a single category."""
    client = _client()
    try:
        item = client.get_category(category_id)
    except Exception as e:
        typer.echo(f"Error fetching category {category_id}: {e}", err=True)
        raise typer.Exit(1)
    emit_record(item, output_json)


@category_app.command("products")
def category_products(
    category_id: str = typer.Argument(..., help="Category id."),
    limit: int = typer.Option(100, "--limit", help="Maximum number of results."),
    offset: int = typer.Option(0, "--offset", help="Number of results to skip."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """List the products in a category."""
    client = _client()
    try:
        items = client.list_category_products(category_id, limit=limit, offset=offset)
    except Exception as e:
        typer.echo(f"Error fetching category products: {e}", err=True)
        raise typer.Exit(1)
    emit_list(items, [
        ("Code", "productCode"),
        ("Name", "name"),
        ("Type", "productType"),
    ], "product", output_json)


@category_app.command("add-product")
def category_add_product(
    category_id: str = typer.Argument(..., help="Category id."),
    product_code: str = typer.Argument(..., help="Product code to add."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
):
    """Add a product to a category."""
    client = _client()
    do_write(lambda: client.add_product_to_category(category_id, product_code),
              f"add {product_code} to category {category_id}", yes=yes, output_json=output_json)


@category_app.command("remove-product")
def category_remove_product(
    category_id: str = typer.Argument(..., help="Category id."),
    product_code: str = typer.Argument(..., help="Product code to remove."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
):
    """Remove a product from a category."""
    client = _client()
    do_write(lambda: client.remove_product_from_category(category_id, product_code),
              f"remove {product_code} from category {category_id}",
              confirm=f"Remove {product_code} from category {category_id}?",
              yes=yes, output_json=output_json)


# ----------------------------------------------------------------------
# Rates
# ----------------------------------------------------------------------


@rate_app.command("list")
def list_rates(
    name: Optional[str] = typer.Option(None, "--name", help="Filter by rate name."),
    product: Optional[str] = typer.Option(None, "--product", help="Filter by product code."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """List rates (search by name and/or product)."""
    client = _client()
    try:
        items = client.list_rates(rate_name=name, product_code=product)
    except Exception as e:
        typer.echo(f"Error fetching rates: {e}", err=True)
        raise typer.Exit(1)
    emit_list(items, [
        ("Rate ID", "rateId"),
        ("Name", "name"),
        ("Products", lambda r: len(r.get("productRates") or [])),
    ], "rate", output_json)


@rate_app.command("get")
def get_rate(
    rate_id: str = typer.Argument(..., help="Rate id."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """Show a single rate."""
    client = _client()
    try:
        item = client.get_rate(rate_id)
    except Exception as e:
        typer.echo(f"Error fetching rate {rate_id}: {e}", err=True)
        raise typer.Exit(1)
    emit_record(item, output_json)


@rate_app.command("add-product")
def rate_add_product(
    rate_id: str = typer.Argument(..., help="Rate id."),
    product_code: str = typer.Argument(..., help="Product code to add."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
):
    """Add a product to a rate."""
    client = _client()
    do_write(lambda: client.add_product_to_rate(rate_id, product_code),
              f"add {product_code} to rate {rate_id}", yes=yes, output_json=output_json)


@rate_app.command("remove-product")
def rate_remove_product(
    rate_id: str = typer.Argument(..., help="Rate id."),
    product_code: str = typer.Argument(..., help="Product code to remove."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
):
    """Remove a product from a rate."""
    client = _client()
    do_write(lambda: client.remove_product_from_rate(rate_id, product_code),
              f"remove {product_code} from rate {rate_id}",
              confirm=f"Remove {product_code} from rate {rate_id}?",
              yes=yes, output_json=output_json)


# ----------------------------------------------------------------------
# Resources
# ----------------------------------------------------------------------


@resource_app.command("list")
def list_resources(
    limit: int = typer.Option(100, "--limit", help="Maximum number of results."),
    offset: int = typer.Option(0, "--offset", help="Number of results to skip."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """List resources."""
    client = _client()
    try:
        items = client.list_resources(limit=limit, offset=offset)
    except Exception as e:
        typer.echo(f"Error fetching resources: {e}", err=True)
        raise typer.Exit(1)
    emit_list(items, [
        ("ID", "id"),
        ("Name", "name"),
        ("Type", "type"),
        ("Seats", "seats"),
    ], "resource", output_json)


@resource_app.command("sessions")
def resource_sessions(
    resource_id: str = typer.Argument(..., help="Resource id."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """List the sessions assigned to a resource."""
    client = _client()
    try:
        items = client.list_resource_sessions(resource_id)
    except Exception as e:
        typer.echo(f"Error fetching resource sessions: {e}", err=True)
        raise typer.Exit(1)
    emit_list(items, [
        ("Session ID", "id"),
        ("Start", "startTimeLocal"),
        ("End", "endTimeLocal"),
    ], "session", output_json)


@resource_app.command("for-session")
def resource_for_session(
    session: Optional[str] = typer.Option(None, "--session", help="Session id."),
    product: Optional[str] = typer.Option(None, "--product", help="Product code (with --start/--start-local)."),
    start: Optional[str] = typer.Option(None, "--start", help="Session start (UTC ISO 8601)."),
    start_local: Optional[str] = typer.Option(None, "--start-local", help="Session start, local time."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """List the resources assigned to a session."""
    client = _client()
    try:
        items = client.list_session_resources(
            session_id=session, product_code=product, start_time=start, start_time_local=start_local
        )
    except Exception as e:
        typer.echo(f"Error fetching session resources: {e}", err=True)
        raise typer.Exit(1)
    emit_list(items, [
        ("ID", "id"),
        ("Name", "name"),
        ("Type", "type"),
    ], "resource", output_json)


@resource_app.command("add-session")
def resource_add_session(
    resource_id: str = typer.Argument(..., help="Resource id."),
    session_id: str = typer.Argument(..., help="Session id to assign."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
):
    """Assign a session to a resource."""
    client = _client()
    do_write(lambda: client.add_session_to_resource(resource_id, session_id),
              f"add session {session_id} to resource {resource_id}",
              yes=yes, output_json=output_json)


@resource_app.command("remove-session")
def resource_remove_session(
    resource_id: str = typer.Argument(..., help="Resource id."),
    session_id: str = typer.Argument(..., help="Session id to remove."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
):
    """Remove a session from a resource."""
    client = _client()
    do_write(lambda: client.remove_session_from_resource(resource_id, session_id),
              f"remove session {session_id} from resource {resource_id}",
              confirm=f"Remove session {session_id} from resource {resource_id}?",
              yes=yes, output_json=output_json)


# ----------------------------------------------------------------------
# Manifest (check-in)
# ----------------------------------------------------------------------


@manifest_app.command("order-status")
def order_checkin_status(
    product: str = typer.Option(..., "--product", help="Product code of the session."),
    order: Optional[str] = typer.Option(None, "--order", help="Order number."),
    start: Optional[str] = typer.Option(None, "--start", help="Session start (UTC ISO 8601)."),
    start_local: Optional[str] = typer.Option(None, "--start-local", help="Session start, local time."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """Show an order-session's check-in status."""
    client = _client()
    try:
        item = client.order_checkin_status(product, order, start, start_local)
    except Exception as e:
        typer.echo(f"Error fetching order check-in: {e}", err=True)
        raise typer.Exit(1)
    emit_record(item, output_json)


@manifest_app.command("order-set")
def order_checkin_set(
    product: str = typer.Option(..., "--product", help="Product code of the session."),
    order: Optional[str] = typer.Option(None, "--order", help="Order number."),
    start: Optional[str] = typer.Option(None, "--start", help="Session start (UTC ISO 8601)."),
    start_local: Optional[str] = typer.Option(None, "--start-local", help="Session start, local time."),
    checkin: bool = typer.Option(True, "--checkin/--no-checkin", help="Set checked-in (default) or not."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
):
    """Set an order-session's check-in state."""
    client = _client()
    do_write(lambda: client.set_order_checkin(product, order, start, start_local, checkin),
              "set order check-in", yes=yes, output_json=output_json)


@manifest_app.command("order-remove")
def order_checkin_remove(
    product: str = typer.Option(..., "--product", help="Product code of the session."),
    order: str = typer.Option(..., "--order", help="Order number (required)."),
    start: Optional[str] = typer.Option(None, "--start", help="Session start (UTC ISO 8601)."),
    start_local: Optional[str] = typer.Option(None, "--start-local", help="Session start, local time."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
):
    """Remove an order-session's check-in record."""
    client = _client()
    do_write(lambda: client.remove_order_checkin(order, product, start, start_local),
              f"remove check-in for order {order}",
              confirm=f"Remove check-in for order {order}?", yes=yes, output_json=output_json)


@manifest_app.command("session-status")
def session_checkin_status(
    product: str = typer.Option(..., "--product", help="Product code of the session."),
    start: Optional[str] = typer.Option(None, "--start", help="Session start (UTC ISO 8601)."),
    start_local: Optional[str] = typer.Option(None, "--start-local", help="Session start, local time."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """Show a session's check-in status."""
    client = _client()
    try:
        item = client.session_checkin_status(product, start, start_local)
    except Exception as e:
        typer.echo(f"Error fetching session check-in: {e}", err=True)
        raise typer.Exit(1)
    emit_record(item, output_json)


@manifest_app.command("session-set")
def session_checkin_set(
    product: str = typer.Option(..., "--product", help="Product code of the session."),
    start: Optional[str] = typer.Option(None, "--start", help="Session start (UTC ISO 8601)."),
    start_local: Optional[str] = typer.Option(None, "--start-local", help="Session start, local time."),
    checkin: bool = typer.Option(True, "--checkin/--no-checkin", help="Set checked-in (default) or not."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
):
    """Set a whole session's check-in state."""
    client = _client()
    do_write(lambda: client.set_session_checkin(product, start, start_local, checkin),
              "set session check-in", yes=yes, output_json=output_json)


@manifest_app.command("session-remove")
def session_checkin_remove(
    product: str = typer.Option(..., "--product", help="Product code of the session."),
    start: Optional[str] = typer.Option(None, "--start", help="Session start (UTC ISO 8601)."),
    start_local: Optional[str] = typer.Option(None, "--start-local", help="Session start, local time."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
):
    """Remove a session's check-in record."""
    client = _client()
    do_write(lambda: client.remove_session_checkin(product, start, start_local),
              "remove session check-in",
              confirm="Remove this session's check-in?", yes=yes, output_json=output_json)


# ----------------------------------------------------------------------
# Vouchers (read-only) and company (read-only)
# ----------------------------------------------------------------------


@voucher_app.command("list")
def list_vouchers(
    search: Optional[str] = typer.Option(None, "--search", help="Filter by voucher code; omit for all."),
    limit: int = typer.Option(100, "--limit", help="Maximum number of results."),
    offset: int = typer.Option(0, "--offset", help="Number of results to skip."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """List vouchers. (Creating vouchers is not in the public API; see docs/rezdy.md.)"""
    client = _client()
    try:
        items = client.list_vouchers(search=search or "", limit=limit, offset=offset)
    except Exception as e:
        typer.echo(f"Error fetching vouchers: {e}", err=True)
        raise typer.Exit(1)
    emit_list(items, [
        ("Code", "code"),
        ("Status", "status"),
        ("Issued", "issueDate"),
        ("Expiry", "expiryDate"),
        ("Reference", "internalReference"),
    ], "voucher", output_json)


@voucher_app.command("get")
def get_voucher(
    voucher_code: str = typer.Argument(..., help="Voucher code."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """Show a single voucher by its code."""
    client = _client()
    try:
        item = client.get_voucher(voucher_code)
    except Exception as e:
        typer.echo(f"Error fetching voucher {voucher_code}: {e}", err=True)
        raise typer.Exit(1)
    emit_record(item, output_json)


@company_app.command("get")
def get_company(
    alias: str = typer.Argument(..., help="Company alias."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """Show a company by its alias."""
    client = _client()
    try:
        item = client.get_company_by_alias(alias)
    except Exception as e:
        typer.echo(f"Error fetching company {alias}: {e}", err=True)
        raise typer.Exit(1)
    emit_record(item, output_json)


@company_app.command("find")
def find_company(
    name: str = typer.Argument(..., help="Company name."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """Find a company by its name."""
    client = _client()
    try:
        item = client.get_company_by_name(name)
    except Exception as e:
        typer.echo(f"Error finding company '{name}': {e}", err=True)
        raise typer.Exit(1)
    emit_record(item, output_json)


if __name__ == "__main__":
    app()
