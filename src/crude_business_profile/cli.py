"""Typer CLI root for Google Business Profile: crude-business-profile.

The credential is the token side file; ``[business-profile]`` in config is read
by ``auth`` alone (the OAuth client) and by the review commands
(``default_location``).
"""

from __future__ import annotations

import typer

from crude_business_profile.auth import (
    load_token,
    loopback_authorize,
    manual_authorize,
    save_token,
    token_store_path,
)
from crude_business_profile.client import BusinessProfileError, BusinessProfileSession
from crude_common.claude_command import register_claude_command
from crude_common.config import account, find_config, read_config, resolve_account

app = typer.Typer(
    help="crude-business-profile — your Google Business Profile: locations, reviews and replies.",
    # A traceback must not print frame locals: the session holds the token record.
    pretty_exceptions_show_locals=False,
)

register_claude_command(app)

# A loopback redirect to fall back on when config sets none. A Desktop-app OAuth
# client accepts any localhost port, so nothing is registered on Google's side.
DEFAULT_REDIRECT_URI = "http://localhost:8911/"


def _which() -> str:
    return f"[business-profile.{account()}]" if account() else "[business-profile]"


def _section() -> dict:
    return resolve_account(read_config(find_config()), "business-profile", account())


def _make_client(info: dict) -> BusinessProfileSession:
    return BusinessProfileSession(info)


def _session() -> BusinessProfileSession:
    """The session on the side file's token; one line and exit 1 when there is none."""
    info = load_token(account())
    if not info:
        typer.echo(
            f"Error: no Google token at {token_store_path(account())}; run "
            "`crude-business-profile auth`, or copy a working authorised-user token "
            "file (google-auth's shape) to that path.", err=True)
        raise typer.Exit(1)
    return _make_client(info)


def default_location():
    """The config's ``default_location`` for the selected account, or None."""
    try:
        return _section().get("default_location")
    except typer.Exit:
        return None


@app.command()
def auth(
    manual: bool = typer.Option(
        False, "--manual", help="Paste-based flow for a headless box (no local web server)."),
    no_browser: bool = typer.Option(
        False, "--no-browser", help="Print the consent URL instead of opening a browser."),
):
    """Consent once as the Google account that manages the business; writes the token file.

    Needs `client_id` and `client_secret` of a Desktop-app OAuth client under
    [business-profile], in a Cloud project granted Business Profile API access
    with the Account Management, Business Information and Google My Business
    APIs enabled, its consent screen published (in Testing, refresh tokens
    expire after seven days). A working token file copied into place serves
    instead of this command.
    """
    cfg = _section()
    client_id, client_secret = cfg.get("client_id"), cfg.get("client_secret")
    if not (client_id and client_secret):
        typer.echo(f"Error: {_which()} must set client_id and client_secret.", err=True)
        raise typer.Exit(1)
    redirect_uri = cfg.get("redirect_uri") or DEFAULT_REDIRECT_URI
    try:
        if manual:
            info = manual_authorize(client_id, client_secret, redirect_uri)
        else:
            info = loopback_authorize(client_id, client_secret, redirect_uri,
                                      open_browser=not no_browser)
    except Exception as e:
        typer.echo(f"Error: auth failed: {e}", err=True)
        raise typer.Exit(1)
    save_token(account(), info)
    typer.echo(f"Authorized; token saved to {token_store_path(account())}.")
    try:
        accounts = _make_client(info).accounts()
    except BusinessProfileError as e:
        typer.echo(f"(could not list the accounts: {e})", err=True)
        return
    for acct in accounts:
        typer.echo(f"  {acct.get('accountName', '?')}  ({acct.get('name')})")


from crude_business_profile import cli_resources, cli_status  # noqa: E402

cli_status.register(app)
app.add_typer(cli_resources.location, name="location")
app.add_typer(cli_resources.review, name="review")


if __name__ == "__main__":
    app()
