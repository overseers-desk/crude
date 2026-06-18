"""Fixed Assets API resource sub-apps for crude-xero.

`register(app)` attaches the Assets product sub-apps: `asset` (list/get/create),
`asset-type` (list/create), and `asset-settings` (get — a read-only singleton).
The uniform get/create verbs are built by `_resource`, mirroring the Accounting
CLI's factory trimmed to the verbs these resources share (assets and asset types
are create-only — the API has no update or delete). The irregular verbs are added
explicitly to the returned sub-app: `asset list` because Xero requires a `status`
filter (DRAFT|REGISTERED|DISPOSED) and pages on its own `{pagination, items}`
envelope, `asset-type list` because that collection is a bare array, and
`asset-settings get` because settings is a single object. The Assets product is
reached through the `XeroClient.assets` facade group, like cli_accounting reaches
`.accounting`.
"""

from __future__ import annotations

from typing import Optional

import typer

from crude_common.output import emit_list, emit_record
from crude_common.writeio import do_write, read_data

# The statuses the Assets API accepts on the (required) list filter.
ASSET_STATUSES = ("DRAFT", "REGISTERED", "DISPOSED")


def _client(*args, **kwargs):
    """The configured Xero client (lazily, to avoid an import cycle with cli)."""
    from crude_xero.cli import _client as _impl

    return _impl(*args, **kwargs)


def _assets():
    """The Assets method group off the configured client facade (`.assets`)."""
    return _client().assets


def _a(label: str) -> str:
    """The indefinite article for a resource label, for readable help text."""
    return "an" if label[:1].lower() in "aeiou" else "a"


def _resource(
    app: typer.Typer,
    name: str,
    label: str,
    *,
    get_fn: Optional[str] = None,
    create_fn: Optional[str] = None,
) -> typer.Typer:
    """Create a resource sub-app with the standard get/create verbs and return it.

    Mirrors the Accounting CLI's `_resource`, trimmed to the verbs the Assets
    resources share. Irregular verbs (the status-filtered/bare-array lists, the
    settings singleton) are added to the returned sub-app by the caller.
    """
    sub = typer.Typer(help=f"Xero {label}.")
    app.add_typer(sub, name=name)

    if get_fn:

        @sub.command("get", help=f"Show a single {label}.")
        def _get(
            guid: str = typer.Argument(..., help=f"{label} id (GUID)."),
            output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
        ):
            try:
                item = getattr(_assets(), get_fn)(guid)
            except Exception as e:
                typer.echo(f"Error fetching {label} {guid}: {e}", err=True)
                raise typer.Exit(1)
            emit_record(item, output_json)

    if create_fn:

        @sub.command("create", help=f"Create {_a(label)} {label} from a JSON body.")
        def _create(
            data: Optional[str] = typer.Option(None, "--data", help=f"{label} object as JSON (or -f / stdin)."),
            file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
            yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
            output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
        ):
            body = read_data(data, file)
            do_write(
                lambda: getattr(_assets(), create_fn)(body),
                f"create {name}",
                confirm=f"Create this {name}?",
                yes=yes,
                output_json=output_json,
            )

    return sub


# ----------------------------------------------------------------------
# register
# ----------------------------------------------------------------------


def register(app: typer.Typer) -> None:
    """Attach the Assets resource sub-apps to the root app."""

    asset = _resource(app, "asset", "asset", get_fn="get_asset", create_fn="create_asset")

    @asset.command("list", help="List assets in one status (Xero requires a status filter).")
    def _asset_list(
        status: str = typer.Option(
            "REGISTERED", "--status",
            help="Asset status to list: DRAFT, REGISTERED or DISPOSED (required by Xero).",
        ),
        page: Optional[int] = typer.Option(None, "--page", help="1-based page number."),
        limit: Optional[int] = typer.Option(None, "--limit", help="Page size: max records to return (pageSize)."),
        order_by: Optional[str] = typer.Option(None, "--order-by", help="Field to sort by (orderBy)."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
    ):
        key = status.upper()
        if key not in ASSET_STATUSES:
            typer.echo(
                f"Error: --status must be one of {', '.join(ASSET_STATUSES)}, got {status!r}.",
                err=True,
            )
            raise typer.Exit(1)
        try:
            items = _assets().list_assets(key, page=page, page_size=limit, order_by=order_by)
        except Exception as e:
            typer.echo(f"Error fetching assets: {e}", err=True)
            raise typer.Exit(1)
        emit_list(
            items,
            [("ID", "assetId"), ("Number", "assetNumber"), ("Name", "assetName"),
             ("Status", "assetStatus"), ("Purchased", "purchaseDate"), ("Price", "purchasePrice")],
            "asset", output_json,
        )

    asset_type = _resource(app, "asset-type", "asset type", create_fn="create_asset_type")

    @asset_type.command("list", help="List asset types.")
    def _asset_type_list(
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
    ):
        try:
            items = _assets().list_asset_types()
        except Exception as e:
            typer.echo(f"Error fetching asset types: {e}", err=True)
            raise typer.Exit(1)
        emit_list(
            items,
            [("ID", "assetTypeId"), ("Name", "assetTypeName"), ("FixedAcct", "fixedAssetAccountId")],
            "asset type", output_json,
        )

    settings = typer.Typer(help="Xero fixed-asset settings (read-only singleton).")
    app.add_typer(settings, name="asset-settings")

    @settings.command("get", help="Show the fixed-asset settings.")
    def _settings_get(
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
    ):
        try:
            item = _assets().get_settings()
        except Exception as e:
            typer.echo(f"Error fetching asset settings: {e}", err=True)
            raise typer.Exit(1)
        emit_record(item, output_json)
