"""Typer CLI for the Airwallex REST API: crude-airwallex.

Defines its own root callback (--version/--account/--on-behalf-of) following the
crude-xero precedent: --account picks the ``[airwallex]`` / ``[airwallex.<name>]``
credential set, and the hidden --on-behalf-of sends the x-on-behalf-of header for a
platform acting on a connected account (a direct account leaves it unset). The
core treasury sub-apps (account, balance, transaction) are attached below; the
payouts, payments-acceptance, and issuing groups are added as those modules land.
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
from crude_common.cliutil import _emit_list, _emit_record
from crude_common.config import (
    account,
    find_config,
    read_config,
    resolve_account,
    set_account,
)
from crude_common.localtime import format_local, to_utc_iso
from crude_airwallex import auth
from crude_airwallex.client import AirwallexAuthError, AirwallexClient, AirwallexError, AirwallexSession

app = typer.Typer(
    help="crude-airwallex — Airwallex global payments and transactions over the REST API."
)

# The --on-behalf-of selection for this invocation, set by the root callback.
# Process global for the same reason as config._account: one binary, one process,
# and the callback fires before any command builds a client.
_on_behalf_of: Optional[str] = None

# JSON option shared by every read command.
_JSON = typer.Option(False, "--json", help="Print the raw JSON of the result.")


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
# Local-time rendering helpers
# ----------------------------------------------------------------------


def _ts(field: str):
    """A list column that renders an ISO-8601 timestamp field in local time."""
    return lambda item: format_local(item.get(field))


def _localize(item: dict, ts_fields) -> dict:
    """A copy of a record with known ISO timestamp fields rendered in local time."""
    if not isinstance(item, dict):
        return item
    out = dict(item)
    for f in ts_fields:
        if out.get(f) is not None:
            out[f] = format_local(out[f])
    return out


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


# ----------------------------------------------------------------------
# account
# ----------------------------------------------------------------------

account_app = typer.Typer(help="The connected Airwallex account.")
app.add_typer(account_app, name="account")


@account_app.command("get")
def account_get(output_json: bool = _JSON):
    """Show the connected account's details."""
    rec = _client().core.get_account()
    _emit_record(_localize(rec, ("created_at", "updated_at")), output_json)


# ----------------------------------------------------------------------
# balance
# ----------------------------------------------------------------------

balance_app = typer.Typer(help="Airwallex balances.")
app.add_typer(balance_app, name="balance")


@balance_app.command("current")
def balance_current(output_json: bool = _JSON):
    """Current balance per held currency."""
    items = _client().core.list_current_balances()
    _emit_list(
        items,
        [
            ("Currency", "currency"),
            ("Total", "total_amount"),
            ("Available", "available_amount"),
            ("Pending", "pending_amount"),
            ("Reserved", "reserved_amount"),
        ],
        "balance",
        output_json,
    )


@balance_app.command("history")
def balance_history(
    currency: Optional[str] = typer.Option(None, "--currency", help="Filter by currency."),
    from_: Optional[str] = typer.Option(None, "--from", help="From date YYYY-MM-DD (local)."),
    to: Optional[str] = typer.Option(None, "--to", help="To date YYYY-MM-DD (local, inclusive)."),
    limit: Optional[int] = typer.Option(None, "--limit", help="Maximum entries to return."),
    output_json: bool = _JSON,
):
    """Balance-affecting entries (the ledger behind the balance)."""
    items = _client().core.list_balance_history(
        currency=currency,
        from_=to_utc_iso(from_) if from_ else None,
        to=to_utc_iso(to, end=True) if to else None,
        limit=limit,
    )
    _emit_list(
        items,
        [
            ("Currency", "currency"),
            ("Amount", "amount"),
            ("Balance", "balance"),
            ("Source", "source_type"),
            ("Description", "description"),
            ("Posted", _ts("posted_at")),
        ],
        "entry",
        output_json,
    )


# ----------------------------------------------------------------------
# transaction
# ----------------------------------------------------------------------

txn_app = typer.Typer(help="Airwallex financial transactions.")
app.add_typer(txn_app, name="transaction")


@txn_app.command("list")
def transaction_list(
    currency: Optional[str] = typer.Option(None, "--currency", help="Filter by currency."),
    status: Optional[str] = typer.Option(None, "--status", help="Filter by status."),
    from_: Optional[str] = typer.Option(None, "--from", help="From date YYYY-MM-DD (local)."),
    to: Optional[str] = typer.Option(None, "--to", help="To date YYYY-MM-DD (local, inclusive)."),
    all_: bool = typer.Option(False, "--all", help="Fetch every page, not just the first."),
    limit: Optional[int] = typer.Option(None, "--limit", help="Maximum transactions to return."),
    output_json: bool = _JSON,
):
    """List financial transactions (filters: currency, status, --from/--to date)."""
    items = _client().core.list_financial_transactions(
        currency=currency,
        status=status,
        from_=to_utc_iso(from_) if from_ else None,
        to=to_utc_iso(to, end=True) if to else None,
        all_pages=all_,
        limit=limit,
    )
    _emit_list(
        items,
        [
            ("ID", "id"),
            ("Currency", "currency"),
            ("Amount", "amount"),
            ("Net", "net"),
            ("Fee", "fee"),
            ("Status", "status"),
            ("Type", "transactionType"),
            ("Created", _ts("createdAt")),
        ],
        "transaction",
        output_json,
    )


@txn_app.command("get")
def transaction_get(
    txn_id: str = typer.Argument(..., help="Financial transaction id."),
    output_json: bool = _JSON,
):
    """Show one financial transaction by id."""
    # The financial_transactions endpoint returns camelCase fields (createdAt,
    # transactionType, settledAt), unlike the snake_case balances/account endpoints.
    rec = _client().core.get_financial_transaction(txn_id)
    _emit_record(_localize(rec, ("createdAt", "settledAt", "estimatedSettledAt")), output_json)


if __name__ == "__main__":
    app()
