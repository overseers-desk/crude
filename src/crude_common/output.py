"""Shared result presentation for the crude site CLIs.

The canonical home for rendering a list or a record as a rich table or raw JSON,
so every `crude-<site> <resource> list`/`get` honours `--json` and the count
line identically. The table-drawing primitives come from rich; this module owns
the project's presentation contract on top of them. Write-verb I/O lives in
crude_common.writeio.
"""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from crude_common.config import s

console = Console()


def emit_list(items: list, columns: list, what: str, output_json: bool,
              header_style: str = "bold magenta", ldif=None) -> None:
    """Render a list of records as a table, or raw JSON with --json.

    `columns` is a list of (header, key) where key is a field name or a callable
    taking the record. With `ldif` set (an LdifSink), the records are written as
    LDIF instead and no table, JSON or count line is printed.
    """
    if ldif is not None:
        _emit_ldif(items, output_json, ldif)
        return
    if output_json:
        typer.echo(json.dumps(items, indent=2, default=str))
        return
    table = Table(show_header=True, header_style=header_style)
    for header, _ in columns:
        table.add_column(header)
    for it in items:
        row = []
        for _, key in columns:
            val = key(it) if callable(key) else it.get(key)
            row.append(s(val))
        table.add_row(*row)
    console.print(table)
    typer.echo(f"\n{len(items)} {what}(s) found.")


def _emit_ldif(items: list, output_json: bool, sink) -> None:
    if output_json:
        raise typer.BadParameter("--json and LDIF output are mutually exclusive")
    from crude_common.ldif import emit_ldif
    emit_ldif(items, sink.pm, sink.site, sink.tz, sink.base_dn)


def emit_record(item: dict, output_json: bool, ldif=None) -> None:
    if ldif is not None:
        _emit_ldif([item], output_json, ldif)
        return
    if output_json:
        typer.echo(json.dumps(item, indent=2, default=str))
        return
    render_record(item)


def render_record(item: dict) -> None:
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
            value_str = s(value)
        if len(value_str) > 200:
            value_str = value_str[:197] + "..."
        table.add_row(key, value_str)
    console.print(table)
