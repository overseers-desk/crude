"""The ``catalog`` sub-app for crude-clover: dump items, categories, modifiers.

The dump exposes the per-item category the orders endpoint omits, so flatten can
resolve a line item's ``item.id`` to its raw Clover category. It does not bucket
into report categories; that aggregation lives in the consuming analysis.
"""

from __future__ import annotations

import json

import typer

from crude_common import asof
from crude_clover.client import CloverError

catalog_app = typer.Typer(help="Clover catalog (inventory items, categories, modifiers).")


def _client():
    """The configured Clover client (lazily, to avoid an import cycle with cli)."""
    from crude_clover.cli import _client as _impl

    return _impl()


@catalog_app.command("dump")
def dump(
    output: str = typer.Option(..., "-o", "--output", help="Write the catalog JSON to this path."),
):
    """Dump the catalog (items with categories, categories, modifier groups) to a file."""
    try:
        data = _client().catalog.dump()
    except CloverError as e:
        typer.echo(f"Error dumping catalog: {e}", err=True)
        raise typer.Exit(1)
    # The catalog is mutable current-state with no audit filter; under a bound
    # the dump file itself carries the disclosure for downstream readers.
    if asof.active():
        data[asof.MARKER_KEY] = asof.CURRENT_STATE
        asof.emit_current_state("the catalog")
    with open(output, "w") as f:
        json.dump(data, f)
    typer.echo(
        f"Wrote catalog ({len(data['items'])} items, "
        f"{len(data['categories'])} categories) to {output}.",
        err=True,
    )


def register(app: typer.Typer) -> None:
    """Attach the catalog group to the root app under ``catalog``."""
    app.add_typer(catalog_app, name="catalog")
