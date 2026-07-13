"""The ``orders`` sub-app for crude-clover: paginated order reads to a file.

A year of orders is tens of MB, so range and incremental pulls go to a required
``-o`` file as JSONL (one Order per line), never to stdout. ``get`` prints a
single order. ``list`` pulls a date range (``--from``/``--to``), or everything
changed since a timestamp (``--since``); ``--compare`` also pulls the matching
364-day-prior period for a like-for-like window.
"""

from __future__ import annotations

import datetime as dt
import json

import typer

from crude_common import asof
from crude_clover.client import CloverError
from crude_clover.orders import day_windows

orders_app = typer.Typer(help="Clover orders (line items, payments, refunds expanded).")


def _client():
    """The configured Clover client (lazily, to avoid an import cycle with cli)."""
    from crude_clover.cli import _client as _impl

    return _impl()


def _pull_range(client, from_, to, tz, path) -> int:
    """Pull a range to JSONL. Under WORLD_AS_OF the windows are clamped at the
    API layer (server-exact on createdTime); this adds the belt-and-braces
    client drop and the modifiedTime>bound flag on each row written, so the
    file itself carries the honesty markers a downstream reader needs."""
    total = 0
    dropped = mutated = 0
    with open(path, "w") as out:
        for start_ms, end_ms in day_windows(from_, to, tz):
            orders, d, m = asof.post_filter(
                list(client.orders.iter_orders(start_ms, end_ms)),
                "createdTime", "modifiedTime")
            dropped += d
            mutated += m
            for order in orders:
                out.write(json.dumps(order, separators=(",", ":")) + "\n")
                total += 1
    asof.emit_notice("order", dropped, mutated)
    return total


@orders_app.command("list")
def list_(
    from_: str = typer.Option(None, "--from", help="From date YYYY-MM-DD (in --tz, inclusive)."),
    to: str = typer.Option(None, "--to", help="To date YYYY-MM-DD (in --tz, inclusive)."),
    tz: str = typer.Option("Australia/Brisbane", "--tz", help="IANA timezone for the date bounds."),
    output: str = typer.Option(..., "-o", "--output", help="Write the JSONL to this path (required)."),
    since: int = typer.Option(
        None, "--since", help="Incremental: orders with modifiedTime >= this epoch ms."),
    compare: bool = typer.Option(
        False, "--compare", help="Also pull the matching 364-day-prior period to <output>.prior."),
):
    """Pull orders to a JSONL file: a date range, or everything since a timestamp."""
    if since is not None and asof.active():
        # --since is explicitly a live-sync tool (modifiedTime>=): its whole
        # point is to pull mutations, which a bounded run must not observe.
        asof.refuse("--since pulls live mutations (modifiedTime>=) and is "
                    "incompatible with a bound; use --from/--to")
    client = _client()
    try:
        if since is not None:
            total = 0
            high = since
            with open(output, "w") as out:
                for order in client.orders.iter_modified_since(since):
                    out.write(json.dumps(order, separators=(",", ":")) + "\n")
                    total += 1
                    high = max(high, order.get("modifiedTime") or 0)
            from crude_common.config import account
            from crude_common.statestore import atomic_write, state_path
            mark = state_path("clover_orders_since", account())
            atomic_write(mark, str(high))
            typer.echo(f"Wrote {total} orders modified since {since} to {output}. "
                       f"Next --since: {high} (saved to {mark}).", err=True)
            return

        if not (from_ and to):
            typer.echo("Error: provide --from and --to, or --since EPOCH_MS.", err=True)
            raise typer.Exit(1)

        total = _pull_range(client, from_, to, tz, output)
        typer.echo(f"Wrote {total} orders to {output}.", err=True)

        if compare:
            shift = dt.timedelta(days=364)
            p_from = (dt.date.fromisoformat(from_) - shift).isoformat()
            p_to = (dt.date.fromisoformat(to) - shift).isoformat()
            p_path = output + ".prior"
            p_total = _pull_range(client, p_from, p_to, tz, p_path)
            typer.echo(f"Wrote {p_total} prior-period orders ({p_from}..{p_to}) to {p_path}.", err=True)
    except CloverError as e:
        typer.echo(f"Error fetching orders: {e}", err=True)
        raise typer.Exit(1)


@orders_app.command("get")
def get(
    order_id: str = typer.Argument(..., help="Order id."),
    expand: str = typer.Option(
        "lineItems.modifications,payments,refunds", "--expand", help="Expand related objects."),
    output: str = typer.Option(None, "-o", "--output", help="Write to this path instead of stdout."),
):
    """Pretty-print one order with all expansions (refund/dispute investigation)."""
    try:
        order = _client().orders.get(order_id, expand=expand)
    except CloverError as e:
        typer.echo(f"Error fetching order {order_id}: {e}", err=True)
        raise typer.Exit(1)
    order = asof.check_record(order, "createdTime", "modifiedTime", what="order")
    text = json.dumps(order, indent=2, default=str)
    if output:
        with open(output, "w") as f:
            f.write(text)
        typer.echo(f"Wrote order {order_id} to {output}.", err=True)
    else:
        typer.echo(text)


def register(app: typer.Typer) -> None:
    """Attach the orders group to the root app under ``orders``."""
    app.add_typer(orders_app, name="orders")
