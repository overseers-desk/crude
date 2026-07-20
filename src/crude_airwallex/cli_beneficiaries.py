"""Payouts beneficiary sub-app for crude-airwallex: list/get/create/update/delete.

`register(app)` attaches the `beneficiary` sub-app over ``/api/v1/beneficiaries``.
Reads render with `emit_list`/`emit_record`; the writes go through `do_write`/
`merge_update`, confirm-before-write (a beneficiary change edits the production
account's saved recipients). Field names are snake_case (verified live); timestamp
columns localize via `crude_airwallex.render`.
"""

from __future__ import annotations

from typing import Optional

import typer

from crude_common import asof
from crude_common.config import (
    account,
    find_config,
    read_config,
    resolve_account,
    resolve_base_dn,
    resolve_timezone,
)
from crude_common.ldif import LdifSink, PersonMap
from crude_common.output import emit_list, emit_record
from crude_common.writeio import do_write, merge_update, read_data
from crude_common.localtime import to_utc_iso
from crude_airwallex.render import localize, ts

_JSON = typer.Option(False, "--json", help="Print the raw JSON of the result.")
_LDIF = typer.Option(False, "--ldif", help="Output LDIF (inetOrgPerson) instead of a table.")


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


def _nested(b: dict, field: str) -> str:
    """A field out of the nested `beneficiary` object (first_name, last_name)."""
    return (b.get("beneficiary") or {}).get(field) or ""


def _cn(b: dict) -> str:
    """The display name: the bank account_name, else the nested first/last name."""
    name = _bank(b, "account_name")
    if name:
        return name
    return " ".join(x for x in (_nested(b, "first_name"), _nested(b, "last_name")) if x)


def _mail(b: dict) -> str:
    """A contact email if the beneficiary carries one (top-level or additional_info)."""
    ben = b.get("beneficiary") or {}
    return (ben.get("email")
            or (ben.get("additional_info") or {}).get("personal_email")
            or "")


def _phone(b: dict) -> str:
    ben = b.get("beneficiary") or {}
    return ben.get("phone_number") or ben.get("phone") or ""


# Only PERSONAL payees are people; COMPANY rows are skipped with a stderr note.
_PERSON_PM = PersonMap(
    attrs={
        "cn": _cn,
        "givenName": lambda b: _nested(b, "first_name"),
        "sn": lambda b: _nested(b, "last_name"),
        "mail": _mail,
        "telephoneNumber": _phone,
    },
    id_key="beneficiary_id",
    created="created_at",
    modified="updated_at",
    include=lambda b: b.get("payer_entity_type") == "PERSONAL",
)


def _ldif_sink() -> LdifSink:
    """Build the LDIF sink for the selected account (timezone and base DN from config)."""
    cfg = read_config(find_config())
    site_cfg = resolve_account(cfg, "airwallex", account())
    return LdifSink(_PERSON_PM, "airwallex",
                    resolve_timezone(cfg, site_cfg), resolve_base_dn(cfg))


@beneficiary_app.command("list")
def beneficiary_list(
    entity_type: Optional[str] = typer.Option(None, "--entity-type", help="Filter by entity type (COMPANY/PERSONAL)."),
    from_: Optional[str] = typer.Option(None, "--from", help="From date YYYY-MM-DD (local)."),
    to: Optional[str] = typer.Option(None, "--to", help="To date YYYY-MM-DD (local, inclusive)."),
    all_: bool = typer.Option(False, "--all", help="Fetch every page, not just the first."),
    limit: Optional[int] = typer.Option(None, "--limit", help="Maximum beneficiaries to return."),
    output_json: bool = _JSON,
    ldif: bool = _LDIF,
):
    """List saved beneficiaries (filters: entity type, --from/--to date)."""
    items = _client().beneficiaries.list_beneficiaries(
        entity_type=entity_type,
        from_=to_utc_iso(from_) if from_ else None,
        to=to_utc_iso(to, end=True) if to else None,
        all_pages=all_,
        limit=limit,
    )
    emit_list(
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
        ldif=_ldif_sink() if ldif else None,
    )


@beneficiary_app.command("get")
def beneficiary_get(
    beneficiary_id: str = typer.Argument(..., help="Beneficiary id."),
    output_json: bool = _JSON,
    ldif: bool = _LDIF,
):
    """Show one beneficiary by id."""
    rec = _client().beneficiaries.get_beneficiary(beneficiary_id)
    rec = asof.check_record(rec, "created_at", "updated_at", what="beneficiary")
    if ldif:
        # LDIF parses the raw ISO timestamps itself, so pass the record before
        # the local-time rendering that emit_record would otherwise show.
        emit_record(rec, output_json, ldif=_ldif_sink())
        return
    emit_record(localize(rec, ("created_at", "updated_at")), output_json)


@beneficiary_app.command("create")
def beneficiary_create(
    data: Optional[str] = typer.Option(None, "--data", help="Beneficiary object as JSON (or -f / stdin)."),
    file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = _JSON,
):
    """Create a beneficiary from a JSON body."""
    body = read_data(data, file)
    do_write(
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
    merge_update(
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
    do_write(
        lambda: _client().beneficiaries.delete_beneficiary(beneficiary_id),
        f"delete beneficiary {beneficiary_id}",
        confirm=f"Delete beneficiary {beneficiary_id}?",
        yes=yes,
        output_json=output_json,
    )


def register(app: typer.Typer) -> None:
    """Attach the beneficiary sub-app to the root app."""
    app.add_typer(beneficiary_app, name="beneficiary")
