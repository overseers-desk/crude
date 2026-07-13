"""Typer CLI for the Xero public APIs: crude-xero.

Defines its own root callback (``--version``/``--account``/``--tenant``) following
the launcher precedent, because Xero needs a third selection axis: ``--account``
picks the OAuth connection (a ``[xero]`` / ``[xero.<name>]`` credential set),
``--tenant`` picks the organisation among the tenants that connection can reach.
The Accounting resource sub-apps and the cross-cutting attachment/history sub-apps
are attached by ``cli_accounting`` and ``cli_crosscutting`` at the bottom.
"""

from __future__ import annotations

import time
from typing import Optional

import typer
from rich.table import Table

from crude_common import asof
from crude_common.claude_command import (
    ACCOUNT_HELP,
    VERSION_HELP,
    add_install_command,
    refresh,
    version_callback,
)
from crude_common.output import console
from crude_common.config import (
    account,
    find_config,
    read_config,
    resolve_account,
    s,
    set_account,
    write_config,
)
from crude_xero.auth import (
    list_connections,
    load_tokens,
    loopback_authorize,
    manual_authorize,
    save_tokens,
)
from crude_xero.client import XeroClient, XeroError, XeroSession

# A localhost-loopback redirect to fall back on when config sets none. It must
# match a redirect URI registered on the Xero app; the user may override it in
# config (the port is derived from the URI).
DEFAULT_REDIRECT_URI = "http://localhost:8910/callback"

# A sensible accounting+payroll read+write grant when config sets no `scopes`.
# offline_access is required for refresh tokens; journals/reports/budgets are
# read-only scopes. The BankFeeds (`bankfeeds`) and Finance (`finance.*`) scopes
# are deliberately absent: those products are access-gated, so requesting their
# scopes in the default consent returns invalid_scope and breaks `crude-xero
# auth`. The user adds them to config `scopes` once Xero grants access (see
# docs/xero.md).
DEFAULT_SCOPES = (
    "openid profile email offline_access "
    "accounting.transactions accounting.contacts accounting.settings "
    "accounting.attachments accounting.journals.read accounting.reports.read "
    "accounting.budgets.read "
    "files assets projects "
    "payroll.employees payroll.payruns payroll.payslip "
    "payroll.timesheets payroll.settings"
)

app = typer.Typer(
    help="crude-xero — Xero accounting (and Payroll, Files, Assets, Projects, "
    "BankFeeds, Finance) over the public APIs."
)

# The --tenant selection for this invocation, set by the root callback. Process
# global for the same reason as config._account: one binary, one process, and the
# callback fires before any command builds a client.
_tenant: Optional[str] = None


def _tenant_opt() -> Optional[str]:
    return _tenant


@app.callback()
def _root(
    version: bool = typer.Option(
        None, "--version", callback=version_callback, is_eager=True, help=VERSION_HELP
    ),
    account: Optional[str] = typer.Option(
        None, "--account", "-a", envvar="CRUDE_ACCOUNT", help=ACCOUNT_HELP
    ),
    tenant: Optional[str] = typer.Option(
        None,
        "--tenant",
        "-t",
        envvar="CRUDE_XERO_TENANT",
        help="Select the Xero organisation (tenant) by name or id; default the sole connection.",
    ),
):
    asof.check_env()
    set_account(account)
    global _tenant
    _tenant = tenant
    refresh()


add_install_command(app)


# ----------------------------------------------------------------------
# Client construction and tenant resolution
# ----------------------------------------------------------------------


def _build_session(xero: dict) -> XeroSession:
    """A tenant-less XeroSession for the selected account, or error if unconfigured.

    Shared by the client builder and the tenant commands, which need a session to
    call ``/connections`` before any tenant is chosen.
    """
    client_id = xero.get("client_id")
    client_secret = xero.get("client_secret")
    if not (client_id and client_secret):
        which = f"[xero.{account()}]" if account() else "[xero]"
        typer.echo(f"Error: {which} must set client_id and client_secret.", err=True)
        raise typer.Exit(1)
    tokens = load_tokens(account(), xero)
    if not tokens or not tokens.get("refresh_token"):
        typer.echo("No Xero tokens; run `crude-xero auth`.", err=True)
        raise typer.Exit(1)
    return XeroSession(account(), client_id, client_secret, tokens)


def _resolve_tenant(xero_section: dict, session: XeroSession, requested: Optional[str]) -> str:
    """Resolve a tenant id: explicit request, then config default, then sole connection.

    A request (or config ``default_tenant``) matches a connection by tenantId
    (uuid) or case-insensitive tenantName. No or ambiguous match, or several
    reachable tenants with none chosen, errors listing the reachable options.
    Calling connections() may trigger a token refresh; that is expected.
    """
    want = requested or xero_section.get("default_tenant")
    conns = session.connections()
    if not conns:
        typer.echo(
            "Error: this token reaches no Xero organisations; run `crude-xero auth`.",
            err=True,
        )
        raise typer.Exit(1)
    if want:
        matches = [
            c
            for c in conns
            if c.get("tenantId") == want
            or (c.get("tenantName") or "").lower() == str(want).lower()
        ]
        if len(matches) == 1:
            return matches[0]["tenantId"]
        typer.echo(f"Error: no single tenant matches {want!r}. Reachable:", err=True)
        _echo_tenant_lines(conns)
        raise typer.Exit(1)
    if len(conns) == 1:
        return conns[0]["tenantId"]
    typer.echo(
        "Error: several Xero organisations are reachable; pick one with --tenant/-t "
        "or pin one with `crude-xero tenant use <name>`. Reachable:",
        err=True,
    )
    _echo_tenant_lines(conns)
    raise typer.Exit(1)


def _make_client(config: dict, tenant: Optional[str] = None) -> XeroClient:
    """Build a XeroClient for the selected account and resolved tenant."""
    xero = resolve_account(config, "xero", account())
    session = _build_session(xero)
    session.tenant_id = _resolve_tenant(xero, session, tenant or _tenant_opt())
    return XeroClient(session)


def _client(tenant: Optional[str] = None) -> XeroClient:
    """The configured Xero client for the selected account and tenant."""
    return _make_client(read_config(find_config()), tenant=tenant)


# ----------------------------------------------------------------------
# Tenant presentation
# ----------------------------------------------------------------------


def _echo_tenant_lines(conns: list) -> None:
    """List reachable tenants to stderr, for the resolution errors."""
    for c in conns:
        typer.echo(
            f"  {s(c.get('tenantName'))}  {s(c.get('tenantId'))}  {s(c.get('tenantType'))}",
            err=True,
        )


def _print_tenants(conns: list, default: Optional[str] = None) -> None:
    """Print the reachable tenants as a table, marking the configured default."""
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Name")
    table.add_column("ID")
    table.add_column("Type")
    table.add_column("Default")
    for c in conns:
        is_default = bool(default) and (
            c.get("tenantId") == default
            or (c.get("tenantName") or "").lower() == str(default).lower()
        )
        table.add_row(
            s(c.get("tenantName")),
            s(c.get("tenantId")),
            s(c.get("tenantType")),
            "*" if is_default else "",
        )
    console.print(table)
    typer.echo(f"\n{len(conns)} tenant(s).")


# ----------------------------------------------------------------------
# Auth and tenant commands
# ----------------------------------------------------------------------


def _durable_tokens(grant: dict) -> dict:
    """Map a raw token-endpoint grant to the durable side-file shape (epoch expiry)."""
    now = time.time()
    return {
        "access_token": grant.get("access_token"),
        "refresh_token": grant.get("refresh_token"),
        "obtained_at": now,
        "expires_at": now + int(grant.get("expires_in", 1800)),
        "scope": grant.get("scope", ""),
    }


@app.command()
def auth(
    manual: bool = typer.Option(
        False, "--manual", help="Paste-based flow for a headless box (no local web server)."
    ),
    no_browser: bool = typer.Option(
        False, "--no-browser", help="Print the consent URL instead of opening a browser."
    ),
):
    """Authorize crude-xero against Xero (OAuth2 browser consent).

    Prerequisite on developer.xero.com (My Apps, this app): register the redirect
    URI (config `redirect_uri`, default http://localhost:8910/callback) as an
    allowed redirect URI, and enable the OAuth scopes requested (config `scopes`,
    or a default accounting read+write grant). Reads work under read scopes;
    writes need the write scopes enabled, then a fresh `crude-xero auth`.
    """
    config = read_config(find_config())
    xero = resolve_account(config, "xero", account())
    client_id = xero.get("client_id")
    client_secret = xero.get("client_secret")
    if not (client_id and client_secret):
        which = f"[xero.{account()}]" if account() else "[xero]"
        typer.echo(f"Error: {which} must set client_id and client_secret.", err=True)
        raise typer.Exit(1)
    redirect_uri = xero.get("redirect_uri") or DEFAULT_REDIRECT_URI
    scopes = xero.get("scopes") or DEFAULT_SCOPES
    if not xero.get("scopes"):
        typer.echo(f"No [xero] scopes set; requesting the default grant:\n  {scopes}")
    if not xero.get("redirect_uri"):
        typer.echo(f"No [xero] redirect_uri set; using {redirect_uri} (register it on the app).")
    try:
        if manual:
            grant = manual_authorize(client_id, client_secret, redirect_uri, scopes)
        else:
            grant = loopback_authorize(
                client_id, client_secret, redirect_uri, scopes, open_browser=not no_browser
            )
    except Exception as e:
        typer.echo(f"Error: auth failed: {e}", err=True)
        raise typer.Exit(1)
    tokens = _durable_tokens(grant)
    save_tokens(account(), tokens)
    typer.echo("Authorized. Connected organisations:")
    try:
        conns = list_connections(tokens["access_token"])
    except Exception as e:
        typer.echo(f"(could not list connections: {e})", err=True)
        conns = []
    _print_tenants(conns, default=xero.get("default_tenant"))
    typer.echo(
        "\nSelect an organisation with --tenant/-t, or pin one: "
        "`crude-xero tenant use <name|id>`."
    )


@app.command()
def tenants():
    """List the Xero organisations the current token can reach."""
    config = read_config(find_config())
    xero = resolve_account(config, "xero", account())
    session = _build_session(xero)
    try:
        conns = session.connections()
    except XeroError as e:
        typer.echo(f"Error fetching connections: {e}", err=True)
        raise typer.Exit(1)
    _print_tenants(conns, default=xero.get("default_tenant"))


tenant_app = typer.Typer(help="Pin the default Xero organisation (tenant) into config.")
app.add_typer(tenant_app, name="tenant")


@tenant_app.command("use")
def tenant_use(
    name: str = typer.Argument(..., help="Tenant name or id to pin as the default."),
):
    """Pin a default tenant for this account (a rare, user-initiated config write)."""
    path = find_config()
    config = read_config(path)
    xero = resolve_account(config, "xero", account())
    session = _build_session(xero)
    try:
        conns = session.connections()
    except XeroError as e:
        typer.echo(f"Error fetching connections: {e}", err=True)
        raise typer.Exit(1)
    matches = [
        c
        for c in conns
        if c.get("tenantId") == name
        or (c.get("tenantName") or "").lower() == name.lower()
    ]
    if len(matches) != 1:
        typer.echo(f"Error: no single tenant matches {name!r}. Reachable:", err=True)
        _echo_tenant_lines(conns)
        raise typer.Exit(1)
    chosen = matches[0]
    section = config.setdefault("xero", {})
    if account():
        section = section.setdefault(account(), {})
    section["default_tenant"] = chosen["tenantId"]
    write_config(path, config)
    typer.echo(
        f"Default tenant set to {s(chosen.get('tenantName'))} ({chosen['tenantId']})."
    )


# Attach the Accounting resource sub-apps, the Files/Assets/Projects sub-apps, and
# the cross-cutting sub-apps.
from crude_xero import (  # noqa: E402
    cli_accounting,
    cli_assets,
    cli_bankfeeds,
    cli_crosscutting,
    cli_files,
    cli_finance,
    cli_payroll,
    cli_projects,
)

cli_accounting.register(app)
cli_files.register(app)
cli_assets.register(app)
cli_projects.register(app)
cli_payroll.register(app)
cli_bankfeeds.register(app)
cli_finance.register(app)
cli_crosscutting.register(app)


if __name__ == "__main__":
    app()
