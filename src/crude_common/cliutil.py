"""Shared CLI presentation and write-body helpers for the crude site CLIs.

The canonical home for the table/record rendering and JSON write-body plumbing
that the rezdy CLI grew first: resolving a write body from --data/-f/stdin,
confirm-before-write, read-merge-write for full-object updates, and rendering a
list or a record as a rich table or raw JSON. crude-xero is the first consumer;
the existing site CLIs keep their own copies for now.
"""

from __future__ import annotations

import json
import sys
from typing import Callable, Optional

import typer
from rich.console import Console
from rich.table import Table

from crude_common.config import s

console = Console()


def _read_data(data: Optional[str], file: Optional[str], required: bool = True) -> dict:
    """Resolve a write body: --data inline JSON, then -f file, then stdin.

    With required False (the update verbs, where typed flags may carry the whole
    change) an absent body yields an empty dict and stdin is left alone.
    """
    if data is not None:
        raw = data
    elif file is not None:
        with open(file, "r") as f:
            raw = f.read()
    elif required and not sys.stdin.isatty():
        raw = sys.stdin.read()
    else:
        if required:
            typer.echo("Error: provide JSON via --data, -f/--file, or stdin.", err=True)
            raise typer.Exit(1)
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError as e:
        typer.echo(f"Error: invalid JSON: {e}", err=True)
        raise typer.Exit(1)
    if not isinstance(parsed, dict):
        typer.echo("Error: JSON body must be an object.", err=True)
        raise typer.Exit(1)
    return parsed


def _do_write(action: Callable, what: str, *, confirm: Optional[str] = None,
              yes: bool = False, output_json: bool = False) -> None:
    """Write command body: confirm if asked, run the action, report the outcome."""
    if confirm and not yes:
        typer.confirm(confirm, abort=True)
    try:
        result = action()
    except Exception as e:
        typer.echo(f"Error: {what}: {e}", err=True)
        raise typer.Exit(1)
    if output_json:
        typer.echo(json.dumps(result if result is not None else {"ok": True}, indent=2, default=str))
        return
    typer.echo(f"{what}: done.")
    if isinstance(result, dict) and result:
        typer.echo(json.dumps(result, default=str))


def _merge_update(get_fn: Callable, update_fn: Callable, data: Optional[str],
                  file: Optional[str], flags: dict, what: str, yes: bool,
                  output_json: bool) -> None:
    """Fetch a record, overlay flags and --data, write it back.

    A flag left at None is not part of the change; an explicit empty string is
    (so --terms "" clears the field).
    """
    current = get_fn()
    if not current:
        typer.echo(f"Error: {what}: record not found.", err=True)
        raise typer.Exit(1)
    overlay = _read_data(data, file, required=False)
    overlay.update({k: v for k, v in flags.items() if v is not None})
    if not overlay:
        typer.echo("Error: nothing to update; pass a flag or --data.", err=True)
        raise typer.Exit(1)
    merged = {**current, **overlay}
    _do_write(lambda: update_fn(merged), what, confirm=f"{what}?", yes=yes, output_json=output_json)


def _emit_list(items: list, columns: list, what: str, output_json: bool,
               header_style: str = "bold magenta") -> None:
    """Render a list of records as a table, or raw JSON with --json.

    `columns` is a list of (header, key) where key is a field name or a callable
    taking the record.
    """
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


def _emit_record(item: dict, output_json: bool) -> None:
    if output_json:
        typer.echo(json.dumps(item, indent=2, default=str))
        return
    _render_record(item)


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
            value_str = s(value)
        if len(value_str) > 200:
            value_str = value_str[:197] + "..."
        table.add_row(key, value_str)
    console.print(table)
