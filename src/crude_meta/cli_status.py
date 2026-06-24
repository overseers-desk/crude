"""Account discovery and heartbeat for crude-meta.

``account show`` resolves and prints the Page and Instagram ids the other
commands rely on (the /me/accounts discovery, or the configured fallbacks).
``status`` confirms the token and reports the Instagram publishing quota, so a
publish run can check headroom against the rolling 24-hour cap.
"""

from __future__ import annotations

import typer

from crude_common.output import emit_record
from crude_meta.client import MetaError

_JSON = typer.Option(False, "--json", help="Print the raw JSON of the result.")

account_app = typer.Typer(help="Resolve the managed Page and Instagram account.")


def _session():
    from crude_meta.cli import _session as impl

    return impl()


@account_app.command("show", help="Show the resolved Page id, name, and Instagram user id.")
def account_show(output_json: bool = _JSON):
    sess = _session()
    try:
        page = sess.page
        rec = {
            "page_id": sess.page_id,
            "page_name": page.get("name"),
            "ig_user_id": sess.ig_user_id,
        }
    except MetaError as e:
        typer.echo(f"Error resolving account: {e}", err=True)
        raise typer.Exit(1)
    emit_record(rec, output_json)


def status(output_json: bool = _JSON):
    """Confirm the token and report the Instagram publishing quota."""
    sess = _session()
    try:
        ig = sess.ig_user_id
        limit = sess.get(
            f"/{ig}/content_publishing_limit",
            params={"fields": "quota_usage,config"},
        ).get("data", [])
    except MetaError as e:
        typer.echo(f"Token check failed: {e}", err=True)
        raise typer.Exit(1)
    row = limit[0] if limit else {}
    rec = {
        "page_id": sess.page_id,
        "ig_user_id": ig,
        "publish_quota_usage": row.get("quota_usage"),
        "publish_quota_total": (row.get("config") or {}).get("quota_total"),
    }
    if not output_json:
        typer.echo("Token valid.")
    emit_record(rec, output_json)


def register(app_root: typer.Typer) -> None:
    """Attach the account sub-app and the status command to the root."""
    app_root.add_typer(account_app, name="account")
    app_root.command("status")(status)
