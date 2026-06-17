"""Payouts FX sub-apps for crude-airwallex: fx-rate current, conversion list/get/create.

`register(app)` attaches `fx-rate` (the read-only current rate) and `conversion`
(over ``/api/v1/fx/conversions``). `conversion create` MOVES REAL MONEY and is
confirm-gated; the rest are read-only. ``--amount`` on `fx-rate current` is the
sell-side amount (the rate endpoint defaults it to 10,000 when omitted). Field names
are snake_case (verified live); timestamp columns localize via
`crude_airwallex.render`.
"""

from __future__ import annotations

from typing import Optional

import typer

from crude_common.cliutil import _do_write, _emit_list, _emit_record, _read_data
from crude_common.localtime import to_utc_iso
from crude_airwallex.render import localize, ts

_JSON = typer.Option(False, "--json", help="Print the raw JSON of the result.")


def _client():
    """The configured Airwallex client (lazily, to avoid an import cycle with cli)."""
    from crude_airwallex.cli import _client as _impl

    return _impl()


# ----------------------------------------------------------------------
# fx-rate (read-only current rate)
# ----------------------------------------------------------------------

fx_rate_app = typer.Typer(help="Airwallex FX rates.")


@fx_rate_app.command("current")
def fx_rate_current(
    buy: str = typer.Option(..., "--buy", help="Buy currency (3-letter ISO, e.g. USD)."),
    sell: str = typer.Option(..., "--sell", help="Sell currency (3-letter ISO, e.g. AUD)."),
    amount: Optional[float] = typer.Option(None, "--amount", help="Sell-side amount (defaults to 10,000)."),
    output_json: bool = _JSON,
):
    """Show the current indicative rate for a buy/sell currency pair."""
    rec = _client().fx.get_current_rate(buy_currency=buy, sell_currency=sell, sell_amount=amount)
    _emit_record(localize(rec, ("created_at",)), output_json)


# ----------------------------------------------------------------------
# conversion
# ----------------------------------------------------------------------

conversion_app = typer.Typer(help="Airwallex FX conversions.")


@conversion_app.command("list")
def conversion_list(
    from_: Optional[str] = typer.Option(None, "--from", help="From date YYYY-MM-DD (local)."),
    to: Optional[str] = typer.Option(None, "--to", help="To date YYYY-MM-DD (local, inclusive)."),
    all_: bool = typer.Option(False, "--all", help="Fetch every page, not just the first."),
    limit: Optional[int] = typer.Option(None, "--limit", help="Maximum conversions to return."),
    output_json: bool = _JSON,
):
    """List booked FX conversions (filters: --from/--to date)."""
    items = _client().fx.list_conversions(
        from_=to_utc_iso(from_) if from_ else None,
        to=to_utc_iso(to, end=True) if to else None,
        all_pages=all_,
        limit=limit,
    )
    _emit_list(
        items,
        [
            ("ID", "conversion_id"),
            ("Buy", "buy_currency"),
            ("Buy Amt", "buy_amount"),
            ("Sell", "sell_currency"),
            ("Sell Amt", "sell_amount"),
            ("Rate", "client_rate"),
            ("Status", "status"),
            ("Created", ts("created_at")),
        ],
        "conversion",
        output_json,
    )


@conversion_app.command("get")
def conversion_get(
    conversion_id: str = typer.Argument(..., help="Conversion id."),
    output_json: bool = _JSON,
):
    """Show one conversion by id."""
    rec = _client().fx.get_conversion(conversion_id)
    _emit_record(localize(rec, ("created_at", "updated_at", "settlement_cutoff_at")), output_json)


@conversion_app.command("create")
def conversion_create(
    data: Optional[str] = typer.Option(None, "--data", help="Conversion object as JSON (or -f / stdin)."),
    file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = _JSON,
):
    """Book a conversion from a JSON body. MOVES REAL MONEY."""
    body = _read_data(data, file)
    _do_write(
        lambda: _client().fx.create_conversion(body),
        "create conversion",
        confirm="Book this conversion? (moves real money)",
        yes=yes,
        output_json=output_json,
    )


def register(app: typer.Typer) -> None:
    """Attach the fx-rate and conversion sub-apps to the root app."""
    app.add_typer(fx_rate_app, name="fx-rate")
    app.add_typer(conversion_app, name="conversion")
