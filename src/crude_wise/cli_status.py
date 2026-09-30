"""Heartbeat for crude-wise.

``status`` confirms the token reaches Wise and prints the profile the other
commands act on, so a revoked token or a config pointing at the wrong profile is
caught with one clean call before any resource command runs. The currencies held
are listed; the amounts are not, since a balance is a now-value.
"""

from __future__ import annotations

import typer

from crude_common.output import emit_record
from crude_wise.client import WiseError

_JSON = typer.Option(False, "--json", help="Print the raw JSON of the result.")


def _session():
    from crude_wise.cli import _session as impl

    return impl()


def status(output_json: bool = _JSON):
    """Confirm the token and print the profile in use and the currencies held."""
    sess = _session()
    try:
        prof = sess.profile()
        currencies = sorted({b.get("currency") for b in sess.balances() if b.get("currency")})
    except WiseError as e:
        typer.echo(f"Credential check failed: {e}", err=True)
        raise typer.Exit(1)
    rec = {
        "profile_id": prof.get("id"),
        "type": prof.get("type"),
        "name": prof.get("businessName") or prof.get("fullName"),
        "country": (prof.get("address") or {}).get("countryIso2Code"),
        "state": prof.get("currentState"),
        "currencies": ", ".join(currencies),
        "sca_key": sess.private_key or "",
    }
    if not output_json:
        typer.echo("Token valid.")
    emit_record(rec, output_json)


def register(app_root: typer.Typer) -> None:
    """Attach the status command to the root."""
    app_root.command("status")(status)
