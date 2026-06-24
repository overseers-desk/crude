"""Heartbeat for crude-facebook.

``status`` confirms the configured token reaches a Page and prints the resolved
Page id and name, so a misconfigured token or an unreachable Page is caught with
one clean call before any resource command runs.
"""

from __future__ import annotations

import typer

from crude_common.output import emit_record
from crude_facebook.client import FacebookError

_JSON = typer.Option(False, "--json", help="Print the raw JSON of the result.")


def _session():
    from crude_facebook.cli import _session as impl

    return impl()


def status(output_json: bool = _JSON):
    """Confirm the token and print the resolved Page id and name."""
    sess = _session()
    try:
        rec = {"page_id": sess.page_id, "page_name": sess.page.get("name")}
    except FacebookError as e:
        typer.echo(f"Token check failed: {e}", err=True)
        raise typer.Exit(1)
    if not output_json:
        typer.echo("Token valid.")
    emit_record(rec, output_json)


def register(app_root: typer.Typer) -> None:
    """Attach the status command to the root."""
    app_root.command("status")(status)
