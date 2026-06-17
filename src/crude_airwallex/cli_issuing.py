"""Issuing sub-apps for crude-airwallex, namespaced under ``issuing``.

A nested group like the `pa` one: each resource (card, cardholder, authorization,
transaction) is its own typer sub-app under the parent `issuing_app`, and
`register(app)` mounts that parent as ``issuing``. Reads render with `_emit_list`/
`_emit_record`; record views localize their timestamp fields via
`crude_airwallex.render`.

`card create` provisions a real spending instrument and `card update` can freeze /
cancel a live card, so both (and the cardholder create/update) are confirm-gated,
mirroring `transfer create`. The card and cardholder updates take a *partial* JSON
body posted straight through (only the fields to change, e.g.
``{"card_status": "INACTIVE"}``), not a read-merge-write — an issued card carries
read-only fields (the masked number, created_at) the update endpoint rejects on a
round-trip. authorization and transaction are read-only.

Only the masked card number is ever shown (the standard card object's `card_number`;
crude never calls a sensitive-details / full-PAN endpoint). Field names are
snake_case (verified live).
"""

from __future__ import annotations

from typing import Optional

import typer

from crude_common.cliutil import _do_write, _emit_list, _emit_record, _read_data
from crude_common.localtime import to_utc_iso
from crude_airwallex.render import localize, ts

_JSON = typer.Option(False, "--json", help="Print the raw JSON of the result.")

# Timestamp fields localized on the single-record get views (snake_case, verified
# live). Cards and cardholders carry created_at/updated_at; the authorization and
# transaction objects also carry the lifecycle instants below.
_CARD_TS = ("created_at", "updated_at")
_CARDHOLDER_TS = ("created_at", "updated_at")
_AUTH_TS = ("created_at", "updated_at", "transaction_date")
_TXN_TS = ("created_at", "updated_at", "transaction_date", "posted_date")


def _client():
    """The configured Airwallex client (lazily, to avoid an import cycle with cli)."""
    from crude_airwallex.cli import _client as _impl

    return _impl()


def _masked_number(card: dict) -> str:
    """A card's masked number for display: the standard object's `card_number` (a
    masked value), falling back to the webhook-style `masked_card_number`. crude only
    ever shows the masked form, never a full PAN."""
    return card.get("card_number") or card.get("masked_card_number") or ""


issuing_app = typer.Typer(help="Airwallex Issuing (cards a business issues for its own spending).")


# ----------------------------------------------------------------------
# card (a provisioned spending instrument; create/update confirm-gated)
# ----------------------------------------------------------------------

card_app = typer.Typer(help="Airwallex issued cards (the standard object shows a masked number).")


@card_app.command("list")
def card_list(
    status: Optional[str] = typer.Option(None, "--status", help="Filter by card status (e.g. ACTIVE, INACTIVE, CLOSED)."),
    cardholder: Optional[str] = typer.Option(None, "--cardholder", help="Filter by cardholder id."),
    from_: Optional[str] = typer.Option(None, "--from", help="From date YYYY-MM-DD (local)."),
    to: Optional[str] = typer.Option(None, "--to", help="To date YYYY-MM-DD (local, inclusive)."),
    all_: bool = typer.Option(False, "--all", help="Fetch every page, not just the first."),
    limit: Optional[int] = typer.Option(None, "--limit", help="Maximum cards to return."),
    output_json: bool = _JSON,
):
    """List issued cards (filters: status, cardholder, --from/--to date)."""
    items = _client().issuing.list_cards(
        status=status,
        cardholder_id=cardholder,
        from_=to_utc_iso(from_) if from_ else None,
        to=to_utc_iso(to, end=True) if to else None,
        all_pages=all_,
        limit=limit,
    )
    _emit_list(
        items,
        [
            ("ID", "card_id"),
            ("Cardholder", "cardholder_id"),
            ("Name on Card", "name_on_card"),
            ("Number", _masked_number),
            ("Status", "card_status"),
            ("Created", ts("created_at")),
        ],
        "card",
        output_json,
    )


@card_app.command("get")
def card_get(
    card_id: str = typer.Argument(..., help="Card id."),
    output_json: bool = _JSON,
):
    """Show one card by id (the card number is masked)."""
    rec = _client().issuing.get_card(card_id)
    _emit_record(localize(rec, _CARD_TS), output_json)


@card_app.command("create")
def card_create(
    data: Optional[str] = typer.Option(None, "--data", help="Card object as JSON (or -f / stdin)."),
    file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = _JSON,
):
    """Provision a card from a JSON body. PROVISIONS A REAL SPENDING INSTRUMENT."""
    body = _read_data(data, file)
    _do_write(
        lambda: _client().issuing.create_card(body),
        "create card",
        confirm="Provision this card? (creates a real spending instrument)",
        yes=yes,
        output_json=output_json,
    )


@card_app.command("update")
def card_update(
    card_id: str = typer.Argument(..., help="Card id to update."),
    data: Optional[str] = typer.Option(None, "--data", help="Partial JSON of fields to change (or -f / stdin)."),
    file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = _JSON,
):
    """Update a card from a partial JSON body. CHANGES A LIVE CARD (e.g. freeze/cancel).

    Send only the fields to change, e.g. {"card_status": "INACTIVE"} to freeze,
    "CLOSED" to cancel, "ACTIVE" to re-activate; limits ride in authorization_controls.
    """
    body = _read_data(data, file)
    _do_write(
        lambda: _client().issuing.update_card(card_id, body),
        f"update card {card_id}",
        confirm=f"Update card {card_id}? (changes a live card, e.g. freeze/cancel)",
        yes=yes,
        output_json=output_json,
    )


# ----------------------------------------------------------------------
# cardholder (the person a card is issued to)
# ----------------------------------------------------------------------

cardholder_app = typer.Typer(help="Airwallex cardholders (the person a card is issued to).")


@cardholder_app.command("list")
def cardholder_list(
    status: Optional[str] = typer.Option(None, "--status", help="Filter by cardholder status."),
    from_: Optional[str] = typer.Option(None, "--from", help="From date YYYY-MM-DD (local)."),
    to: Optional[str] = typer.Option(None, "--to", help="To date YYYY-MM-DD (local, inclusive)."),
    all_: bool = typer.Option(False, "--all", help="Fetch every page, not just the first."),
    limit: Optional[int] = typer.Option(None, "--limit", help="Maximum cardholders to return."),
    output_json: bool = _JSON,
):
    """List cardholders (filters: status, --from/--to date)."""
    items = _client().issuing.list_cardholders(
        status=status,
        from_=to_utc_iso(from_) if from_ else None,
        to=to_utc_iso(to, end=True) if to else None,
        all_pages=all_,
        limit=limit,
    )
    _emit_list(
        items,
        [
            ("ID", "cardholder_id"),
            ("Name", "name"),
            ("Email", "email"),
            ("Type", "type"),
            ("Status", "status"),
            ("Created", ts("created_at")),
        ],
        "cardholder",
        output_json,
    )


@cardholder_app.command("get")
def cardholder_get(
    cardholder_id: str = typer.Argument(..., help="Cardholder id."),
    output_json: bool = _JSON,
):
    """Show one cardholder by id."""
    rec = _client().issuing.get_cardholder(cardholder_id)
    _emit_record(localize(rec, _CARDHOLDER_TS), output_json)


@cardholder_app.command("create")
def cardholder_create(
    data: Optional[str] = typer.Option(None, "--data", help="Cardholder object as JSON (or -f / stdin)."),
    file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = _JSON,
):
    """Create a cardholder from a JSON body. REGISTERS A REAL PERSON for card issuance."""
    body = _read_data(data, file)
    _do_write(
        lambda: _client().issuing.create_cardholder(body),
        "create cardholder",
        confirm="Create this cardholder? (registers a real person for card issuance)",
        yes=yes,
        output_json=output_json,
    )


@cardholder_app.command("update")
def cardholder_update(
    cardholder_id: str = typer.Argument(..., help="Cardholder id to update."),
    data: Optional[str] = typer.Option(None, "--data", help="Partial JSON of fields to change (or -f / stdin)."),
    file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = _JSON,
):
    """Update a cardholder from a partial JSON body."""
    body = _read_data(data, file)
    _do_write(
        lambda: _client().issuing.update_cardholder(cardholder_id, body),
        f"update cardholder {cardholder_id}",
        confirm=f"Update cardholder {cardholder_id}?",
        yes=yes,
        output_json=output_json,
    )


# ----------------------------------------------------------------------
# authorization (a live card-auth attempt; read-only)
# ----------------------------------------------------------------------

authorization_app = typer.Typer(help="Airwallex card authorizations (live card-auth attempts; read-only).")


@authorization_app.command("list")
def authorization_list(
    status: Optional[str] = typer.Option(None, "--status", help="Filter by status."),
    card: Optional[str] = typer.Option(None, "--card", help="Filter by card id."),
    from_: Optional[str] = typer.Option(None, "--from", help="From date YYYY-MM-DD (local)."),
    to: Optional[str] = typer.Option(None, "--to", help="To date YYYY-MM-DD (local, inclusive)."),
    all_: bool = typer.Option(False, "--all", help="Fetch every page, not just the first."),
    limit: Optional[int] = typer.Option(None, "--limit", help="Maximum authorizations to return."),
    output_json: bool = _JSON,
):
    """List card authorizations (filters: status, card, --from/--to date)."""
    items = _client().issuing.list_authorizations(
        status=status,
        card_id=card,
        from_=to_utc_iso(from_) if from_ else None,
        to=to_utc_iso(to, end=True) if to else None,
        all_pages=all_,
        limit=limit,
    )
    _emit_list(
        items,
        [
            ("ID", "transaction_id"),
            ("Card", "card_id"),
            ("Billing Amt", "billing_amount"),
            ("Billing Ccy", "billing_currency"),
            ("Status", "status"),
            ("Created", ts("created_at")),
        ],
        "authorization",
        output_json,
    )


@authorization_app.command("get")
def authorization_get(
    authorization_id: str = typer.Argument(..., help="Authorization id."),
    output_json: bool = _JSON,
):
    """Show one authorization by id."""
    rec = _client().issuing.get_authorization(authorization_id)
    _emit_record(localize(rec, _AUTH_TS), output_json)


# ----------------------------------------------------------------------
# transaction (a settled card transaction; read-only)
# ----------------------------------------------------------------------

transaction_app = typer.Typer(help="Airwallex card transactions (settled card transactions; read-only).")


@transaction_app.command("list")
def transaction_list(
    status: Optional[str] = typer.Option(None, "--status", help="Filter by status."),
    card: Optional[str] = typer.Option(None, "--card", help="Filter by card id."),
    from_: Optional[str] = typer.Option(None, "--from", help="From date YYYY-MM-DD (local)."),
    to: Optional[str] = typer.Option(None, "--to", help="To date YYYY-MM-DD (local, inclusive)."),
    all_: bool = typer.Option(False, "--all", help="Fetch every page, not just the first."),
    limit: Optional[int] = typer.Option(None, "--limit", help="Maximum transactions to return."),
    output_json: bool = _JSON,
):
    """List settled card transactions (filters: status, card, --from/--to date)."""
    items = _client().issuing.list_transactions(
        status=status,
        card_id=card,
        from_=to_utc_iso(from_) if from_ else None,
        to=to_utc_iso(to, end=True) if to else None,
        all_pages=all_,
        limit=limit,
    )
    _emit_list(
        items,
        [
            ("ID", "transaction_id"),
            ("Card", "card_id"),
            ("Type", "transaction_type"),
            ("Billing Amt", "billing_amount"),
            ("Billing Ccy", "billing_currency"),
            ("Status", "status"),
            ("Created", ts("created_at")),
        ],
        "transaction",
        output_json,
    )


@transaction_app.command("get")
def transaction_get(
    transaction_id: str = typer.Argument(..., help="Card transaction id."),
    output_json: bool = _JSON,
):
    """Show one card transaction by id."""
    rec = _client().issuing.get_transaction(transaction_id)
    _emit_record(localize(rec, _TXN_TS), output_json)


# Assemble the nested issuing group: each resource sub-app under the parent.
issuing_app.add_typer(card_app, name="card")
issuing_app.add_typer(cardholder_app, name="cardholder")
issuing_app.add_typer(authorization_app, name="authorization")
issuing_app.add_typer(transaction_app, name="transaction")


def register(app: typer.Typer) -> None:
    """Attach the Issuing group to the root app under ``issuing``."""
    app.add_typer(issuing_app, name="issuing")
