"""Core treasury resource sub-apps for crude-airwallex: account, balance, transaction.

`register(app)` attaches the account, balance, and transaction sub-apps; all are
read-only. Reads render with the shared `emit_list`/`emit_record`; timestamp
columns and record views localize via `crude_airwallex.render`. Note the casing
split (see docs/airwallex.md): `financial_transactions` returns camelCase fields
(createdAt, transactionType, settledAt), while balances and account are snake_case.
"""

from __future__ import annotations

from typing import Optional

import typer

from crude_common.output import emit_list, emit_record
from crude_common.localtime import to_utc_iso
from crude_airwallex.render import localize, ts

_JSON = typer.Option(False, "--json", help="Print the raw JSON of the result.")


def _client():
    """The configured Airwallex client (lazily, to avoid an import cycle with cli)."""
    from crude_airwallex.cli import _client as _impl

    return _impl()


# ----------------------------------------------------------------------
# account
# ----------------------------------------------------------------------

account_app = typer.Typer(help="The connected Airwallex account.")


@account_app.command("get")
def account_get(output_json: bool = _JSON):
    """Show the connected account's details."""
    rec = _client().core.get_account()
    emit_record(localize(rec, ("created_at", "updated_at")), output_json)


# ----------------------------------------------------------------------
# balance
# ----------------------------------------------------------------------

balance_app = typer.Typer(help="Airwallex balances.")


@balance_app.command("current")
def balance_current(output_json: bool = _JSON):
    """Current balance per held currency."""
    items = _client().core.list_current_balances()
    emit_list(
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
    emit_list(
        items,
        [
            ("Currency", "currency"),
            ("Amount", "amount"),
            ("Balance", "balance"),
            ("Source", "source_type"),
            ("Description", "description"),
            ("Posted", ts("posted_at")),
        ],
        "entry",
        output_json,
    )


# ----------------------------------------------------------------------
# transaction
# ----------------------------------------------------------------------

txn_app = typer.Typer(help="Airwallex financial transactions.")


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
    emit_list(
        items,
        [
            ("ID", "id"),
            ("Currency", "currency"),
            ("Amount", "amount"),
            ("Net", "net"),
            ("Fee", "fee"),
            ("Status", "status"),
            ("Type", "transactionType"),
            ("Created", ts("createdAt")),
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
    rec = _client().core.get_financial_transaction(txn_id)
    emit_record(localize(rec, ("createdAt", "settledAt", "estimatedSettledAt")), output_json)


def register(app: typer.Typer) -> None:
    """Attach the core treasury sub-apps to the root app."""
    app.add_typer(account_app, name="account")
    app.add_typer(balance_app, name="balance")
    app.add_typer(txn_app, name="transaction")
