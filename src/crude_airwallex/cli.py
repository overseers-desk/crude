"""Typer CLI root for the Airwallex REST API: crude-airwallex.

Defines the root callback (--version/--account/--on-behalf-of) following the
crude-xero precedent: --account picks the ``[airwallex]`` / ``[airwallex.<name>]``
credential set, and the hidden --on-behalf-of sends the x-on-behalf-of header for a
platform acting on a connected account (a direct account leaves it unset). Client
construction and the `login` command live here; the resource sub-apps are attached
by the per-group ``cli_<group>.register(app)`` calls at the foot of the file.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import typer

from crude_common.claude_command import (
    ACCOUNT_HELP,
    VERSION_HELP,
    add_install_command,
    refresh,
    version_callback,
)
from crude_common.config import (
    account,
    find_config,
    read_config,
    resolve_account,
    set_account,
)
from crude_airwallex import auth
from crude_airwallex.client import AirwallexAuthError, AirwallexClient, AirwallexError, AirwallexSession

app = typer.Typer(
    help="crude-airwallex — Airwallex global payments and transactions over the REST API."
)

# The --on-behalf-of selection for this invocation, set by the root callback.
# Process global for the same reason as config._account: one binary, one process,
# and the callback fires before any command builds a client.
_on_behalf_of: Optional[str] = None


@app.callback()
def _root(
    version: bool = typer.Option(
        None, "--version", callback=version_callback, is_eager=True, help=VERSION_HELP
    ),
    account_opt: Optional[str] = typer.Option(
        None, "--account", "-a", envvar="CRUDE_ACCOUNT", help=ACCOUNT_HELP
    ),
    on_behalf_of: Optional[str] = typer.Option(
        None,
        "--on-behalf-of",
        "-o",
        envvar="CRUDE_AIRWALLEX_ON_BEHALF_OF",
        hidden=True,
        help="Act on a connected account (platforms only): send x-on-behalf-of with this id.",
    ),
):
    set_account(account_opt)
    global _on_behalf_of
    _on_behalf_of = on_behalf_of
    refresh()


add_install_command(app)


# ----------------------------------------------------------------------
# Client construction
# ----------------------------------------------------------------------


def _build_session(aw: dict) -> AirwallexSession:
    """An AirwallexSession for the selected account, or error if unconfigured."""
    client_id = aw.get("client_id")
    api_key = aw.get("api_key")
    if not (client_id and api_key):
        which = f"[airwallex.{account()}]" if account() else "[airwallex]"
        typer.echo(f"Error: {which} must set client_id and api_key.", err=True)
        raise typer.Exit(1)
    return AirwallexSession(
        account(),
        client_id,
        api_key,
        base=auth.base_url(aw.get("environment")),
        on_behalf_of=_on_behalf_of or aw.get("on_behalf_of"),
        token=auth.load_token(account()),
    )


def _make_client(config: dict) -> AirwallexClient:
    aw = resolve_account(config, "airwallex", account())
    return AirwallexClient(_build_session(aw))


def _client() -> AirwallexClient:
    """The configured Airwallex client for the selected account."""
    return _make_client(read_config(find_config()))


# ----------------------------------------------------------------------
# login
# ----------------------------------------------------------------------


@app.command()
def login():
    """Log in to Airwallex with the configured api key and report token expiry."""
    aw = resolve_account(read_config(find_config()), "airwallex", account())
    session = _build_session(aw)
    try:
        session._login()
    except AirwallexAuthError as e:
        which = f"[airwallex.{account()}]" if account() else "[airwallex]"
        typer.echo(
            f"Error: Airwallex rejected the credentials: {e}. "
            f"Check client_id and api_key under {which} in config.toml.",
            err=True,
        )
        raise typer.Exit(1)
    except AirwallexError as e:
        typer.echo(f"Error: login failed: {e}", err=True)
        raise typer.Exit(1)
    when = datetime.fromtimestamp(session.token["expires_at"]).strftime("%Y-%m-%d %H:%M")
    typer.echo(f"Logged in. Token valid until {when} (local time).")


# Attach the resource sub-apps. Core treasury (account, balance, transaction),
# Payouts (beneficiary, transfer, fx-rate, conversion), and Payments Acceptance (the
# nested `pa` group) ship now; issuing is added as that module lands.
from crude_airwallex import (  # noqa: E402
    cli_beneficiaries,
    cli_core,
    cli_fx,
    cli_payments,
    cli_transfers,
)

cli_core.register(app)
cli_beneficiaries.register(app)
cli_transfers.register(app)
cli_fx.register(app)
cli_payments.register(app)


if __name__ == "__main__":
    app()
