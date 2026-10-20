"""Heartbeat for crude-business-profile.

``status`` confirms the token file yields an access token that reaches Google
and prints the accounts it manages, so a missing file, a dead refresh token, or
a Cloud project without API access is caught with one clean call before any
resource command runs.
"""

from __future__ import annotations

import typer

from crude_business_profile.client import BusinessProfileError
from crude_common import asof
from crude_common.output import emit_record

_JSON = typer.Option(False, "--json", help="Print the raw JSON of the result.")


def _session():
    from crude_business_profile.cli import _session as impl

    return impl()


def status(output_json: bool = _JSON):
    """Confirm the token and print the Google account and the accounts it manages."""
    sess = _session()
    try:
        accounts = sess.accounts()
    except BusinessProfileError as e:
        typer.echo(f"Token check failed: {e}", err=True)
        raise typer.Exit(1)
    rec = {
        "google_account": sess.info.get("account") or "",
        "accounts": ", ".join(
            f"{a.get('accountName', '?')} ({a.get('name')})" for a in accounts),
    }
    rec = asof.current_state(rec, "the account list")
    if not output_json:
        typer.echo("Token valid.")
    emit_record(rec, output_json)


def register(app_root: typer.Typer) -> None:
    """Attach the status command to the root."""
    app_root.command("status")(status)
