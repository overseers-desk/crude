"""Payouts beneficiary sub-app for crude-airwallex: list/get/create/update/delete.

`register(app)` attaches the `beneficiary` sub-app over ``/api/v1/beneficiaries``.
Reads render with `_emit_list`/`_emit_record`; the writes go through `_do_write`/
`_merge_update`, confirm-before-write (a beneficiary change edits the production
account's saved recipients). Field names are snake_case (verified live); timestamp
columns localize via `crude_airwallex.render`.
"""

from __future__ import annotations

from typing import Optional

import typer

from crude_common.cliutil import _do_write, _emit_list, _emit_record, _merge_update, _read_data
from crude_common.localtime import to_utc_iso
from crude_airwallex.render import localize, ts

_JSON = typer.Option(False, "--json", help="Print the raw JSON of the result.")


def _client():
    """The configured Airwallex client (lazily, to avoid an import cycle with cli)."""
    from crude_airwallex.cli import _client as _impl

    return _impl()


beneficiary_app = typer.Typer(help="Airwallex payout beneficiaries (saved recipients).")


def _bank(b: dict, field: str) -> str:
    """A field out of the nested beneficiary.bank_details (e.g. account_name)."""
    return ((b.get("beneficiary") or {}).get("bank_details") or {}).get(field) or ""


def _methods(b: dict) -> str:
    return ", ".join(b.get("payment_methods") or [])


@beneficiary_app.command("list")
def beneficiary_list(
    entity_type: Optional[str] = typer.Option(None, "--entity-type", help="Filter by entity type (COMPANY/PERSONAL)."),
    from_: Optional[str] = typer.Option(None, "--from", help="From date YYYY-MM-DD (local)."),
    to: Optional[str] = typer.Option(None, "--to", help="To date YYYY-MM-DD (local, inclusive)."),
    all_: bool = typer.Option(False, "--all", help="Fetch every page, not just the first."),
    limit: Optional[int] = typer.Option(None, "--limit", help="Maximum beneficiaries to return."),
    output_json: bool = _JSON,
):
    """List saved beneficiaries (filters: entity type, --from/--to date)."""
    items = _client().beneficiaries.list_beneficiaries(
        entity_type=entity_type,
        from_=to_utc_iso(from_) if from_ else None,
        to=to_utc_iso(to, end=True) if to else None,
        all_pages=all_,
        limit=limit,
    )
    _emit_list(
        items,
        [
            ("ID", "beneficiary_id"),
            ("Account", lambda b: _bank(b, "account_name")),
            ("Currency", lambda b: _bank(b, "account_currency")),
            ("Country", lambda b: _bank(b, "bank_country_code")),
            ("Entity", "payer_entity_type"),
            ("Methods", _methods),
        ],
        "beneficiary",
        output_json,
    )


@beneficiary_app.command("get")
def beneficiary_get(
    beneficiary_id: str = typer.Argument(..., help="Beneficiary id."),
    output_json: bool = _JSON,
):
    """Show one beneficiary by id."""
    rec = _client().beneficiaries.get_beneficiary(beneficiary_id)
    _emit_record(localize(rec, ("created_at", "updated_at")), output_json)


@beneficiary_app.command("create")
def beneficiary_create(
    data: Optional[str] = typer.Option(None, "--data", help="Beneficiary object as JSON (or -f / stdin)."),
    file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = _JSON,
):
    """Create a beneficiary from a JSON body."""
    body = _read_data(data, file)
    _do_write(
        lambda: _client().beneficiaries.create_beneficiary(body),
        "create beneficiary",
        confirm="Create this beneficiary?",
        yes=yes,
        output_json=output_json,
    )


@beneficiary_app.command("update")
def beneficiary_update(
    beneficiary_id: str = typer.Argument(..., help="Beneficiary id to update."),
    data: Optional[str] = typer.Option(None, "--data", help="Partial JSON overlaying the fetched record."),
    file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON overlay from a file."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = _JSON,
):
    """Update a beneficiary (read-merge-write)."""
    client = _client().beneficiaries
    _merge_update(
        lambda: client.get_beneficiary(beneficiary_id),
        lambda merged: client.update_beneficiary(beneficiary_id, merged),
        data,
        file,
        {},
        f"update beneficiary {beneficiary_id}",
        yes,
        output_json,
    )


@beneficiary_app.command("delete")
def beneficiary_delete(
    beneficiary_id: str = typer.Argument(..., help="Beneficiary id to delete."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = _JSON,
):
    """Delete a beneficiary by id."""
    _do_write(
        lambda: _client().beneficiaries.delete_beneficiary(beneficiary_id),
        f"delete beneficiary {beneficiary_id}",
        confirm=f"Delete beneficiary {beneficiary_id}?",
        yes=yes,
        output_json=output_json,
    )


def register(app: typer.Typer) -> None:
    """Attach the beneficiary sub-app to the root app."""
    app.add_typer(beneficiary_app, name="beneficiary")
