"""Heartbeat and scope reporting for crude-clover.

``status`` confirms the token resolves a merchant and reports today's order count
and revenue. ``scopes`` probes the token against every registry resource and
reports which permissions are enabled. Reads are probed with a GET; writes only
with ``--probe-writes``, which POSTs to a sentinel element id: an authorised
token gets 404 (no such record, nothing created), an unauthorised one gets
401/403, so write scope is detected without mutating anything.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import typer
from rich.console import Console
from rich.table import Table

from crude_common import asof
from crude_clover.client import CloverError
from crude_clover.orders import day_windows
from crude_clover.resources import REGISTRY

console = Console()

# POST target that cannot create: an update to an id that does not exist.
_SCOPE_PROBE_ID = "__crude_scope_probe__"


def classify_write(code: int) -> str:
    """Map a write-probe status to a scope verdict. 401/403 means the token lacks
    write scope; 400/404/422 means it is authorised (the request was rejected for
    content or a missing record, creating nothing)."""
    if code == 429:
        return "rate-limited"
    if code in (401, 403):
        return "blocked"
    if code in (400, 404, 422):
        return "enabled"
    if code in (200, 201):
        return "enabled-unexpected"
    return "undetermined"


def _client():
    from crude_clover.cli import _client as _impl

    return _impl()


def status(
    tz: str = typer.Option("Australia/Brisbane", "--tz", help="IANA timezone for 'today'."),
):
    """Confirm the token and report today's order count and revenue."""
    client = _client()
    try:
        merchant = client.resources.get("")  # /v3/merchants/{mId}
    except CloverError as e:
        typer.echo(f"Token check failed: {e}", err=True)
        raise typer.Exit(1)
    today = datetime.now(ZoneInfo(tz)).date().isoformat()
    count = 0
    revenue = 0
    try:
        for start_ms, end_ms in day_windows(today, today, tz):
            for order in client.orders.iter_orders(start_ms, end_ms):
                count += 1
                revenue += order.get("total", 0) or 0
    except CloverError as e:
        typer.echo(f"Error reading today's orders: {e}", err=True)
        raise typer.Exit(1)
    typer.echo(f"Token valid. Merchant: {merchant.get('name')} ({merchant.get('id')}).")
    typer.echo(f"Today ({today}, {tz}): {count} orders, ${revenue / 100:,.2f}.")


def scopes(
    probe_writes: bool = typer.Option(
        False, "--probe-writes",
        help="Also probe write scope. Sends POSTs that create nothing; opt-in."),
):
    """Report which read (and optionally write) scopes the token has."""
    if probe_writes and asof.active():
        # The write probe sends live POST calls (to a nonexistent id, creating
        # nothing, but write calls all the same); a bounded run must not touch
        # the live present. Run `scopes` without --probe-writes for read scope.
        asof.refuse("--probe-writes sends live write calls (POSTs); it is "
                    "refused under a bound — run `scopes` without it for read scope")
    if probe_writes:
        typer.confirm(
            "Probe write scope? This sends POST requests to the live API. They target a "
            "nonexistent id and create nothing, but they are write calls. Continue?",
            abort=True,
        )
    client = _client()
    sess = client.session
    mid = sess.merchant_id
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("resource")
    table.add_column("read")
    table.add_column("write")
    blocked_writes = []
    for spec in REGISTRY:
        path = f"/v3/merchants/{mid}" + (f"/{spec.segment}" if spec.segment else "")
        r = sess.probe("GET", path, params=[("limit", 1)])
        read = "enabled" if r == 200 else ("rate-limited" if r == 429 else f"blocked ({r})")
        if not spec.writable:
            write = "-"
        elif not probe_writes:
            write = "writable (not probed)"
        else:
            w = sess.probe("POST", f"{path}/{_SCOPE_PROBE_ID}", json={})
            verdict = classify_write(w)
            write = f"{verdict} ({w})"
            if verdict == "blocked":
                blocked_writes.append(spec.name)
        table.add_row(spec.name, read, write)
    console.print(table)
    if probe_writes and blocked_writes:
        typer.echo(
            "\nWrite scope missing for: " + ", ".join(blocked_writes) + "."
            "\nEnable Write on the matching resource (Inventory, Orders, Customers, "
            "Employees) in the AP dashboard: Setup -> API Tokens, then reissue the token.")
    elif probe_writes:
        typer.echo("\nAll probed write scopes are enabled.")


def register(app_root: typer.Typer) -> None:
    """Attach the status and scopes commands to the root app."""
    app_root.command("status")(status)
    app_root.command("scopes")(scopes)
