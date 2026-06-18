"""Payouts transfer sub-app for crude-airwallex: list/get/create.

`register(app)` attaches the `transfer` sub-app over ``/api/v1/transfers``. `list`
and `get` are read-only; `create` MOVES REAL MONEY and is confirm-gated with an
explicit money warning (mirroring how crude-xero gates `invoice email`). Field names
are snake_case (verified live); timestamp columns localize via
`crude_airwallex.render`.
"""

from __future__ import annotations

from typing import Optional

import typer

from crude_common.output import emit_list, emit_record
from crude_common.writeio import do_write, read_data
from crude_common.localtime import to_utc_iso
from crude_airwallex.render import localize, ts

_JSON = typer.Option(False, "--json", help="Print the raw JSON of the result.")


def _client():
    """The configured Airwallex client (lazily, to avoid an import cycle with cli)."""
    from crude_airwallex.cli import _client as _impl

    return _impl()


transfer_app = typer.Typer(help="Airwallex payout transfers.")


@transfer_app.command("list")
def transfer_list(
    status: Optional[str] = typer.Option(None, "--status", help="Filter by status."),
    from_: Optional[str] = typer.Option(None, "--from", help="From date YYYY-MM-DD (local)."),
    to: Optional[str] = typer.Option(None, "--to", help="To date YYYY-MM-DD (local, inclusive)."),
    all_: bool = typer.Option(False, "--all", help="Fetch every page, not just the first."),
    limit: Optional[int] = typer.Option(None, "--limit", help="Maximum transfers to return."),
    output_json: bool = _JSON,
):
    """List transfers (filters: status, --from/--to date)."""
    items = _client().transfers.list_transfers(
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
            ("Beneficiary", "beneficiary_id"),
            ("Amount", "transfer_amount"),
            ("Currency", "transfer_currency"),
            ("Method", "transfer_method"),
            ("Status", "status"),
            ("Created", ts("created_at")),
        ],
        "transfer",
        output_json,
    )


@transfer_app.command("get")
def transfer_get(
    transfer_id: str = typer.Argument(..., help="Transfer id."),
    output_json: bool = _JSON,
):
    """Show one transfer by id."""
    rec = _client().transfers.get_transfer(transfer_id)
    emit_record(localize(rec, ("created_at", "updated_at")), output_json)


@transfer_app.command("create")
def transfer_create(
    data: Optional[str] = typer.Option(None, "--data", help="Transfer object as JSON (or -f / stdin)."),
    file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = _JSON,
):
    """Create a transfer from a JSON body. MOVES REAL MONEY."""
    body = read_data(data, file)
    do_write(
        lambda: _client().transfers.create_transfer(body),
        "create transfer",
        confirm="Create this transfer? (moves real money)",
        yes=yes,
        output_json=output_json,
    )


def register(app: typer.Typer) -> None:
    """Attach the transfer sub-app to the root app."""
    app.add_typer(transfer_app, name="transfer")
