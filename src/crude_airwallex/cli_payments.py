"""Payments Acceptance sub-apps for crude-airwallex, namespaced under ``pa``.

Unlike the flat payouts resources, this group is nested: each resource is its own
typer sub-app attached to a parent `pa_app`, and `register(app)` mounts that parent
as ``pa``. Resources: payment-intent (with confirm/capture/cancel money verbs),
refund, customer, payment-consent (read-only), payment-link.

Reads render with `_emit_list`/`_emit_record`; record views localize their timestamp
fields via `crude_airwallex.render`. The money/creation verbs (payment-intent
create/confirm/capture/cancel, refund create, payment-link create) MOVE REAL MONEY or
publish a public page, so each is confirm-gated with an explicit warning, mirroring
`transfer create`. Field names are snake_case (verified live); the create-body shapes
are passed through verbatim from the caller's JSON.
"""

from __future__ import annotations

from typing import Optional

import typer

from crude_common.cliutil import _do_write, _emit_list, _emit_record, _merge_update, _read_data
from crude_common.localtime import to_utc_iso
from crude_airwallex.render import localize, ts

_JSON = typer.Option(False, "--json", help="Print the raw JSON of the result.")

# Timestamp fields localized on the single-record get views (snake_case, verified
# live). Payment links carry an extra `expires_at` instant (see _LINK_TS).
_TS = ("created_at", "updated_at")
_LINK_TS = ("created_at", "updated_at", "expires_at")


def _client():
    """The configured Airwallex client (lazily, to avoid an import cycle with cli)."""
    from crude_airwallex.cli import _client as _impl

    return _impl()


pa_app = typer.Typer(help="Airwallex Payments Acceptance (the pa product group).")


# ----------------------------------------------------------------------
# payment-intent (request a payment; confirm/capture/cancel move money)
# ----------------------------------------------------------------------

intent_app = typer.Typer(help="Airwallex payment intents (a request to collect a payment).")


@intent_app.command("list")
def intent_list(
    status: Optional[str] = typer.Option(None, "--status", help="Filter by status."),
    from_: Optional[str] = typer.Option(None, "--from", help="From date YYYY-MM-DD (local)."),
    to: Optional[str] = typer.Option(None, "--to", help="To date YYYY-MM-DD (local, inclusive)."),
    all_: bool = typer.Option(False, "--all", help="Fetch every page, not just the first."),
    limit: Optional[int] = typer.Option(None, "--limit", help="Maximum intents to return."),
    output_json: bool = _JSON,
):
    """List payment intents (filters: status, --from/--to date)."""
    items = _client().payments.list_payment_intents(
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
            ("Amount", "amount"),
            ("Currency", "currency"),
            ("Order", "merchant_order_id"),
            ("Status", "status"),
            ("Created", ts("created_at")),
        ],
        "payment intent",
        output_json,
    )


@intent_app.command("get")
def intent_get(
    intent_id: str = typer.Argument(..., help="Payment intent id."),
    output_json: bool = _JSON,
):
    """Show one payment intent by id."""
    rec = _client().payments.get_payment_intent(intent_id)
    _emit_record(localize(rec, _TS), output_json)


@intent_app.command("create")
def intent_create(
    data: Optional[str] = typer.Option(None, "--data", help="Payment intent object as JSON (or -f / stdin)."),
    file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = _JSON,
):
    """Create a payment intent from a JSON body. REQUESTS REAL MONEY from a shopper."""
    body = _read_data(data, file)
    _do_write(
        lambda: _client().payments.create_payment_intent(body),
        "create payment intent",
        confirm="Create this payment intent? (requests real money from a shopper)",
        yes=yes,
        output_json=output_json,
    )


@intent_app.command("confirm")
def intent_confirm(
    intent_id: str = typer.Argument(..., help="Payment intent id to confirm."),
    data: Optional[str] = typer.Option(None, "--data", help="Confirm body as JSON (or -f / stdin)."),
    file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = _JSON,
):
    """Confirm a payment intent. AUTHORIZES A REAL PAYMENT against the shopper."""
    body = _read_data(data, file, required=False)
    _do_write(
        lambda: _client().payments.confirm_payment_intent(intent_id, body),
        f"confirm payment intent {intent_id}",
        confirm=f"Confirm payment intent {intent_id}? (authorizes a real payment)",
        yes=yes,
        output_json=output_json,
    )


@intent_app.command("capture")
def intent_capture(
    intent_id: str = typer.Argument(..., help="Payment intent id to capture."),
    data: Optional[str] = typer.Option(None, "--data", help="Capture body as JSON (or -f / stdin)."),
    file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = _JSON,
):
    """Capture an authorized payment intent. MOVES REAL MONEY (settles the charge)."""
    body = _read_data(data, file, required=False)
    _do_write(
        lambda: _client().payments.capture_payment_intent(intent_id, body),
        f"capture payment intent {intent_id}",
        confirm=f"Capture payment intent {intent_id}? (moves real money)",
        yes=yes,
        output_json=output_json,
    )


@intent_app.command("cancel")
def intent_cancel(
    intent_id: str = typer.Argument(..., help="Payment intent id to cancel."),
    data: Optional[str] = typer.Option(None, "--data", help="Cancel body as JSON (or -f / stdin)."),
    file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = _JSON,
):
    """Cancel a payment intent (voids its authorization)."""
    body = _read_data(data, file, required=False)
    _do_write(
        lambda: _client().payments.cancel_payment_intent(intent_id, body),
        f"cancel payment intent {intent_id}",
        confirm=f"Cancel payment intent {intent_id}? (voids the authorization)",
        yes=yes,
        output_json=output_json,
    )


# ----------------------------------------------------------------------
# refund (give money back against a payment intent)
# ----------------------------------------------------------------------

refund_app = typer.Typer(help="Airwallex refunds (money returned to a shopper).")


@refund_app.command("list")
def refund_list(
    status: Optional[str] = typer.Option(None, "--status", help="Filter by status."),
    from_: Optional[str] = typer.Option(None, "--from", help="From date YYYY-MM-DD (local)."),
    to: Optional[str] = typer.Option(None, "--to", help="To date YYYY-MM-DD (local, inclusive)."),
    all_: bool = typer.Option(False, "--all", help="Fetch every page, not just the first."),
    limit: Optional[int] = typer.Option(None, "--limit", help="Maximum refunds to return."),
    output_json: bool = _JSON,
):
    """List refunds (filters: status, --from/--to date)."""
    items = _client().payments.list_refunds(
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
            ("Intent", "payment_intent_id"),
            ("Amount", "amount"),
            ("Currency", "currency"),
            ("Reason", "reason"),
            ("Status", "status"),
            ("Created", ts("created_at")),
        ],
        "refund",
        output_json,
    )


@refund_app.command("get")
def refund_get(
    refund_id: str = typer.Argument(..., help="Refund id."),
    output_json: bool = _JSON,
):
    """Show one refund by id."""
    rec = _client().payments.get_refund(refund_id)
    _emit_record(localize(rec, _TS), output_json)


@refund_app.command("create")
def refund_create(
    data: Optional[str] = typer.Option(None, "--data", help="Refund object as JSON (or -f / stdin)."),
    file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = _JSON,
):
    """Issue a refund from a JSON body. MOVES REAL MONEY back to the shopper."""
    body = _read_data(data, file)
    _do_write(
        lambda: _client().payments.create_refund(body),
        "create refund",
        confirm="Issue this refund? (moves real money back to the shopper)",
        yes=yes,
        output_json=output_json,
    )


# ----------------------------------------------------------------------
# customer (a saved shopper; no money)
# ----------------------------------------------------------------------

customer_app = typer.Typer(help="Airwallex payment customers (saved shoppers).")


@customer_app.command("list")
def customer_list(
    from_: Optional[str] = typer.Option(None, "--from", help="From date YYYY-MM-DD (local)."),
    to: Optional[str] = typer.Option(None, "--to", help="To date YYYY-MM-DD (local, inclusive)."),
    all_: bool = typer.Option(False, "--all", help="Fetch every page, not just the first."),
    limit: Optional[int] = typer.Option(None, "--limit", help="Maximum customers to return."),
    output_json: bool = _JSON,
):
    """List saved customers (filters: --from/--to date)."""
    items = _client().payments.list_customers(
        from_=to_utc_iso(from_) if from_ else None,
        to=to_utc_iso(to, end=True) if to else None,
        all_pages=all_,
        limit=limit,
    )
    _emit_list(
        items,
        [
            ("ID", "id"),
            ("Merchant Ref", "merchant_customer_id"),
            ("Name", "name"),
            ("Email", "email"),
            ("Phone", "phone_number"),
            ("Created", ts("created_at")),
        ],
        "customer",
        output_json,
    )


@customer_app.command("get")
def customer_get(
    customer_id: str = typer.Argument(..., help="Customer id."),
    output_json: bool = _JSON,
):
    """Show one customer by id."""
    rec = _client().payments.get_customer(customer_id)
    _emit_record(localize(rec, _TS), output_json)


@customer_app.command("create")
def customer_create(
    data: Optional[str] = typer.Option(None, "--data", help="Customer object as JSON (or -f / stdin)."),
    file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = _JSON,
):
    """Create a customer from a JSON body."""
    body = _read_data(data, file)
    _do_write(
        lambda: _client().payments.create_customer(body),
        "create customer",
        confirm="Create this customer?",
        yes=yes,
        output_json=output_json,
    )


@customer_app.command("update")
def customer_update(
    customer_id: str = typer.Argument(..., help="Customer id to update."),
    data: Optional[str] = typer.Option(None, "--data", help="Partial JSON overlaying the fetched record."),
    file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON overlay from a file."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = _JSON,
):
    """Update a customer (read-merge-write)."""
    client = _client().payments
    _merge_update(
        lambda: client.get_customer(customer_id),
        lambda merged: client.update_customer(customer_id, merged),
        data,
        file,
        {},
        f"update customer {customer_id}",
        yes,
        output_json,
    )


@customer_app.command("delete")
def customer_delete(
    customer_id: str = typer.Argument(..., help="Customer id to delete."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = _JSON,
):
    """Delete a customer by id."""
    _do_write(
        lambda: _client().payments.delete_customer(customer_id),
        f"delete customer {customer_id}",
        confirm=f"Delete customer {customer_id}?",
        yes=yes,
        output_json=output_json,
    )


# ----------------------------------------------------------------------
# payment-consent (a saved mandate; read-only)
# ----------------------------------------------------------------------

consent_app = typer.Typer(help="Airwallex payment consents (saved mandates).")


@consent_app.command("list")
def consent_list(
    status: Optional[str] = typer.Option(None, "--status", help="Filter by status."),
    from_: Optional[str] = typer.Option(None, "--from", help="From date YYYY-MM-DD (local)."),
    to: Optional[str] = typer.Option(None, "--to", help="To date YYYY-MM-DD (local, inclusive)."),
    all_: bool = typer.Option(False, "--all", help="Fetch every page, not just the first."),
    limit: Optional[int] = typer.Option(None, "--limit", help="Maximum consents to return."),
    output_json: bool = _JSON,
):
    """List payment consents (filters: status, --from/--to date)."""
    items = _client().payments.list_payment_consents(
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
            ("Customer", "customer_id"),
            ("Status", "status"),
            ("Next By", "next_triggered_by"),
            ("Merchant Trigger", "merchant_trigger_reason"),
            ("Created", ts("created_at")),
        ],
        "payment consent",
        output_json,
    )


@consent_app.command("get")
def consent_get(
    consent_id: str = typer.Argument(..., help="Payment consent id."),
    output_json: bool = _JSON,
):
    """Show one payment consent by id."""
    rec = _client().payments.get_payment_consent(consent_id)
    _emit_record(localize(rec, _TS), output_json)


# ----------------------------------------------------------------------
# payment-link (a hosted page that collects a payment)
# ----------------------------------------------------------------------

link_app = typer.Typer(help="Airwallex payment links (hosted payment pages).")


def _link_currency(link: dict) -> str:
    """A link's currency: `currency` on a fixed link, `default_currency` on a reusable
    multi-currency one (verified live: reusable links carry the latter, not the former)."""
    return link.get("currency") or link.get("default_currency") or ""


@link_app.command("list")
def link_list(
    status: Optional[str] = typer.Option(None, "--status", help="Filter by status."),
    from_: Optional[str] = typer.Option(None, "--from", help="From date YYYY-MM-DD (local)."),
    to: Optional[str] = typer.Option(None, "--to", help="To date YYYY-MM-DD (local, inclusive)."),
    all_: bool = typer.Option(False, "--all", help="Fetch every page, not just the first."),
    limit: Optional[int] = typer.Option(None, "--limit", help="Maximum links to return."),
    output_json: bool = _JSON,
):
    """List payment links (filters: status, --from/--to date)."""
    items = _client().payments.list_payment_links(
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
            ("Title", "title"),
            ("Amount", "amount"),
            ("Currency", _link_currency),
            ("Status", "status"),
            ("URL", "url"),
            ("Created", ts("created_at")),
        ],
        "payment link",
        output_json,
    )


@link_app.command("get")
def link_get(
    link_id: str = typer.Argument(..., help="Payment link id."),
    output_json: bool = _JSON,
):
    """Show one payment link by id."""
    rec = _client().payments.get_payment_link(link_id)
    _emit_record(localize(rec, _LINK_TS), output_json)


@link_app.command("create")
def link_create(
    data: Optional[str] = typer.Option(None, "--data", help="Payment link object as JSON (or -f / stdin)."),
    file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = _JSON,
):
    """Create a payment link from a JSON body. PUBLISHES A PUBLIC PAYMENT PAGE."""
    body = _read_data(data, file)
    _do_write(
        lambda: _client().payments.create_payment_link(body),
        "create payment link",
        confirm="Create this payment link? (publishes a public page that collects real money)",
        yes=yes,
        output_json=output_json,
    )


# Assemble the nested pa group: each resource sub-app under the parent pa_app.
pa_app.add_typer(intent_app, name="payment-intent")
pa_app.add_typer(refund_app, name="refund")
pa_app.add_typer(customer_app, name="customer")
pa_app.add_typer(consent_app, name="payment-consent")
pa_app.add_typer(link_app, name="payment-link")


def register(app: typer.Typer) -> None:
    """Attach the Payments Acceptance group to the root app under ``pa``."""
    app.add_typer(pa_app, name="pa")
