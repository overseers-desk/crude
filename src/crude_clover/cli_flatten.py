"""The ``flatten`` command for crude-clover: orders JSONL to Square-shape CSV.

Local file work only — no Clover call, so it needs no client or token.
"""

from __future__ import annotations

import typer

from crude_clover.flatten import flatten


def register(app: typer.Typer) -> None:
    """Attach the flatten command to the root app."""

    @app.command("flatten")
    def flatten_cmd(
        orders: str = typer.Argument(..., help="Orders JSONL from `orders list`."),
        catalog: str = typer.Option(..., "--catalog", help="Catalog JSON from `catalog dump`."),
        output: str = typer.Option(..., "-o", "--output", help="Write the CSV to this path."),
        tz: str = typer.Option(
            "Australia/Brisbane", "--tz", help="IANA timezone for the Date/Time columns."
        ),
    ):
        """Render Clover orders into the Square item-level CSV shape.

        One row per line item (Category from the catalog), plus one
        negative-Net Sales row per refund. Qty is 1 per line item, or unitQty
        (thousandths) for an item sold by measure.
        """
        written = flatten(orders, catalog, output, tz)
        typer.echo(f"Wrote {written} rows to {output}.", err=True)
