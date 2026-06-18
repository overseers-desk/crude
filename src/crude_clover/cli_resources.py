"""Registry-driven resource sub-apps plus a generic passthrough for crude-clover.

Each ``ResourceSpec`` in ``crude_clover.resources.REGISTRY`` becomes a Typer
sub-app with ``list``/``get`` (and ``create``/``update``/``delete`` when the
resource is writable), built by the same ``_resource`` factory rather than
hand-written, following crude-xero. A generic ``resource <segment> <verb>``
sub-app reaches any endpoint not in the registry, auto-selecting columns as
crude-deputy does, so a new Clover object is reachable without a code change.

Update sends the ``--data`` body directly: Clover's POST-to-element update is a
partial merge server-side, so posting back a fetched object (with its href and
expanded children) would be rejected.
"""

from __future__ import annotations

from typing import List, Optional

import typer
from rich.console import Console
from rich.table import Table

from crude_common.cliutil import _do_write, _emit_list, _emit_record, _read_data
from crude_common.config import s
from crude_clover.client import CloverError
from crude_clover.resources import REGISTRY

console = Console()

_JSON = typer.Option(False, "--json", help="Print the raw JSON of the result.")
_YES = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt.")


def _client():
    """The configured Clover client (lazily, to avoid an import cycle with cli)."""
    from crude_clover.cli import _client as _impl

    return _impl()


def _auto_columns(first: dict) -> list:
    """id then up to six scalar fields, in the order Clover returns them."""
    scalar = [k for k, v in first.items() if not isinstance(v, (dict, list)) and k != "id"]
    return (["id"] if "id" in first else []) + scalar[:6]


def _auto_emit(items: list, output_json: bool, what: str) -> None:
    """Render rows whose fields are not known ahead of time. ``--json`` shows all."""
    if output_json:
        import json

        typer.echo(json.dumps(items, indent=2, default=str))
        return
    if not items:
        typer.echo(f"No {what} found.")
        return
    columns = _auto_columns(items[0])
    table = Table(show_header=True, header_style="bold magenta")
    for col in columns:
        table.add_column(col, style="dim" if col == "id" else None)
    for row in items:
        cells = []
        for col in columns:
            v = row.get(col)
            cell = "(object)" if isinstance(v, dict) else (
                f"{len(v)} item(s)" if isinstance(v, list) else s(v))
            cells.append(cell[:57] + "..." if len(cell) > 60 else cell)
        table.add_row(*cells)
    console.print(table)
    typer.echo(f"\n{len(items)} {what}(s) found.")


# ---------------------------------------------------------------------------
# Registry factory
# ---------------------------------------------------------------------------

def _resource(spec) -> typer.Typer:
    """A Typer sub-app for one registry resource."""
    sub = typer.Typer(help=f"Clover {spec.name}.")
    seg, name, cols = spec.segment, spec.name, spec.columns

    if spec.singleton:
        @sub.command("get", help=f"Show the {name}.")
        def _get_one(output_json: bool = _JSON):
            try:
                rec = _client().resources.get(seg, expand=spec.expand)
            except CloverError as e:
                typer.echo(f"Error: {e}", err=True)
                raise typer.Exit(1)
            _emit_record(rec, output_json)
        return sub

    @sub.command("list", help=f"List {name}.")
    def _list(
        filter_: Optional[List[str]] = typer.Option(
            None, "--filter", help="Clover filter, e.g. name=Coffee (repeatable)."),
        expand: Optional[str] = typer.Option(spec.expand, "--expand", help="Expand related objects."),
        limit: int = typer.Option(100, "--limit", help="Maximum rows (one page) unless --all."),
        all_: bool = typer.Option(False, "--all", help="Fetch every page (to the 10000 cap)."),
        output_json: bool = _JSON,
    ):
        try:
            items = _client().resources.list(
                seg, expand=expand, filters=filter_, limit=limit, all_pages=all_)
        except CloverError as e:
            typer.echo(f"Error fetching {name}: {e}", err=True)
            raise typer.Exit(1)
        _emit_list(items, cols, name, output_json)

    @sub.command("get", help=f"Show one {name} by id.")
    def _get(
        rid: str = typer.Argument(..., help=f"{name} id."),
        expand: Optional[str] = typer.Option(spec.expand, "--expand", help="Expand related objects."),
        output_json: bool = _JSON,
    ):
        try:
            rec = _client().resources.get(seg, rid, expand=expand)
        except CloverError as e:
            typer.echo(f"Error fetching {name} {rid}: {e}", err=True)
            raise typer.Exit(1)
        _emit_record(rec, output_json)

    if spec.writable:
        @sub.command("create", help=f"Create a {name} from a JSON body. MUTATES THE LIVE POS.")
        def _create(
            data: Optional[str] = typer.Option(None, "--data", help="Object as JSON (or -f / stdin)."),
            file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
            yes: bool = _YES,
            output_json: bool = _JSON,
        ):
            body = _read_data(data, file)
            _do_write(lambda: _client().resources.create(seg, body),
                      f"create {name}", confirm=f"Create this {name}? (writes to the live POS)",
                      yes=yes, output_json=output_json)

        @sub.command("update", help=f"Update a {name} (partial; sends your fields only).")
        def _update(
            rid: str = typer.Argument(..., help=f"{name} id to update."),
            data: Optional[str] = typer.Option(None, "--data", help="Changed fields as JSON (or -f / stdin)."),
            file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
            yes: bool = _YES,
            output_json: bool = _JSON,
        ):
            body = _read_data(data, file)
            _do_write(lambda: _client().resources.update(seg, rid, body),
                      f"update {name} {rid}", confirm=f"Update {name} {rid}? (writes to the live POS)",
                      yes=yes, output_json=output_json)

        @sub.command("delete", help=f"Delete a {name} by id. Irreversible.")
        def _delete(
            rid: str = typer.Argument(..., help=f"{name} id to delete."),
            yes: bool = _YES,
            output_json: bool = _JSON,
        ):
            _do_write(lambda: _client().resources.delete(seg, rid),
                      f"delete {name} {rid}", confirm=f"Delete {name} {rid}? (cannot be undone)",
                      yes=yes, output_json=output_json)

    return sub


# ---------------------------------------------------------------------------
# Generic passthrough: resource <segment> <verb>
# ---------------------------------------------------------------------------

resource_app = typer.Typer(
    help="Any Clover resource generically (segment is the API path, e.g. items, modifier_groups).")


@resource_app.command("list")
def _g_list(
    segment: str = typer.Argument(..., help="API path segment, e.g. items, tax_rates."),
    filter_: Optional[List[str]] = typer.Option(None, "--filter", help="Clover filter (repeatable)."),
    expand: Optional[str] = typer.Option(None, "--expand", help="Expand related objects."),
    limit: int = typer.Option(100, "--limit"),
    all_: bool = typer.Option(False, "--all", help="Fetch every page."),
    output_json: bool = _JSON,
):
    """List any resource collection, auto-selecting columns."""
    try:
        items = _client().resources.list(segment, expand=expand, filters=filter_, limit=limit, all_pages=all_)
    except CloverError as e:
        typer.echo(f"Error fetching {segment}: {e}", err=True)
        raise typer.Exit(1)
    _auto_emit(items, output_json, segment)


@resource_app.command("get")
def _g_get(
    segment: str = typer.Argument(..., help="API path segment."),
    rid: str = typer.Argument(..., help="Record id."),
    expand: Optional[str] = typer.Option(None, "--expand"),
    output_json: bool = _JSON,
):
    """Show any record by id."""
    try:
        rec = _client().resources.get(segment, rid, expand=expand)
    except CloverError as e:
        typer.echo(f"Error fetching {segment} {rid}: {e}", err=True)
        raise typer.Exit(1)
    _emit_record(rec, output_json)


@resource_app.command("info")
def _g_info(
    segment: str = typer.Argument(..., help="API path segment."),
):
    """List the field names on the first record of a collection (field discovery)."""
    try:
        items = _client().resources.list(segment, limit=1)
    except CloverError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    if not items:
        typer.echo(f"{segment}: no records to sample.")
        return
    for k, v in items[0].items():
        typer.echo(f"  {k}: {type(v).__name__}")


@resource_app.command("create")
def _g_create(
    segment: str = typer.Argument(..., help="API path segment."),
    data: Optional[str] = typer.Option(None, "--data", help="Object as JSON (or -f / stdin)."),
    file: Optional[str] = typer.Option(None, "-f", "--file"),
    yes: bool = _YES,
    output_json: bool = _JSON,
):
    """Create any record. MUTATES THE LIVE POS."""
    body = _read_data(data, file)
    _do_write(lambda: _client().resources.create(segment, body),
              f"create {segment}", confirm=f"Create this {segment}? (writes to the live POS)",
              yes=yes, output_json=output_json)


@resource_app.command("update")
def _g_update(
    segment: str = typer.Argument(..., help="API path segment."),
    rid: str = typer.Argument(..., help="Record id."),
    data: Optional[str] = typer.Option(None, "--data", help="Changed fields as JSON (or -f / stdin)."),
    file: Optional[str] = typer.Option(None, "-f", "--file"),
    yes: bool = _YES,
    output_json: bool = _JSON,
):
    """Update any record (partial). MUTATES THE LIVE POS."""
    body = _read_data(data, file)
    _do_write(lambda: _client().resources.update(segment, rid, body),
              f"update {segment} {rid}", confirm=f"Update {segment} {rid}? (writes to the live POS)",
              yes=yes, output_json=output_json)


@resource_app.command("delete")
def _g_delete(
    segment: str = typer.Argument(..., help="API path segment."),
    rid: str = typer.Argument(..., help="Record id."),
    yes: bool = _YES,
    output_json: bool = _JSON,
):
    """Delete any record. Irreversible."""
    _do_write(lambda: _client().resources.delete(segment, rid),
              f"delete {segment} {rid}", confirm=f"Delete {segment} {rid}? (cannot be undone)",
              yes=yes, output_json=output_json)


def register(app: typer.Typer) -> None:
    """Attach every registry resource sub-app and the generic passthrough."""
    for spec in REGISTRY:
        app.add_typer(_resource(spec), name=spec.name)
    app.add_typer(resource_app, name="resource")
