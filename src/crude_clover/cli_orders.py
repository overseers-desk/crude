"""The ``orders`` sub-app for crude-clover: a paginated orders pull to a file.

A year of orders is tens of MB, so the pull goes to a required ``-o`` file as
JSONL (one Order per line), never to stdout.
"""

from __future__ import annotations

import json

import typer

from crude_clover.client import CloverError
from crude_clover.orders import day_windows

orders_app = typer.Typer(help="Clover orders (line items, payments, refunds expanded).")


def _client():
    """The configured Clover client (lazily, to avoid an import cycle with cli)."""
    from crude_clover.cli import _client as _impl

    return _impl()


@orders_app.command("list")
def list_(
    from_: str = typer.Option(..., "--from", help="From date YYYY-MM-DD (in --tz, inclusive)."),
    to: str = typer.Option(..., "--to", help="To date YYYY-MM-DD (in --tz, inclusive)."),
    tz: str = typer.Option("Australia/Brisbane", "--tz", help="IANA timezone for the date bounds."),
    output: str = typer.Option(..., "-o", "--output", help="Write the JSONL to this path (required)."),
):
    """Pull orders for a date range to a JSONL file (one Order per line)."""
    client = _client()
    total = 0
    try:
        with open(output, "w") as out:
            for start_ms, end_ms in day_windows(from_, to, tz):
                for order in client.orders.iter_orders(start_ms, end_ms):
                    out.write(json.dumps(order, separators=(",", ":")) + "\n")
                    total += 1
    except CloverError as e:
        typer.echo(f"Error fetching orders: {e}", err=True)
        raise typer.Exit(1)
    typer.echo(f"Wrote {total} orders to {output}.", err=True)


def register(app: typer.Typer) -> None:
    """Attach the orders group to the root app under ``orders``."""
    app.add_typer(orders_app, name="orders")
