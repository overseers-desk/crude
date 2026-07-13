"""Shared write-verb I/O for the crude site CLIs.

The canonical home for the write-command plumbing the rezdy CLI grew first:
resolving a write body from --data/-f/stdin, confirm-before-write, and
read-merge-write for full-object updates. Result presentation lives in
crude_common.output.
"""

from __future__ import annotations

import json
import sys
from typing import Callable, Optional

import typer

from crude_common import asof


def read_data(data: Optional[str], file: Optional[str], required: bool = True) -> dict:
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


def do_write(action: Callable, what: str, *, confirm: Optional[str] = None,
             yes: bool = False, output_json: bool = False) -> None:
    """Write command body: confirm if asked, run the action, report the outcome.

    Refuses outright while WORLD_AS_OF is set: a bounded run reads the past,
    and a write would mutate the live present (see crude_common.asof).
    """
    asof.refuse_write_cli(what)
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


def merge_update(get_fn: Callable, update_fn: Callable, data: Optional[str],
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
    overlay = read_data(data, file, required=False)
    overlay.update({k: v for k, v in flags.items() if v is not None})
    if not overlay:
        typer.echo("Error: nothing to update; pass a flag or --data.", err=True)
        raise typer.Exit(1)
    merged = {**current, **overlay}
    do_write(lambda: update_fn(merged), what, confirm=f"{what}?", yes=yes, output_json=output_json)
