"""Typer CLI for the Skål Australia member portal: crude-skal."""

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from crude_common.claude_command import register_claude_command
from crude_common.config import (
    account as _account,
    find_config as _find_config,
    read_config as _read_config,
    resolve_account as _resolve_account,
    s as _s,
)

app = typer.Typer(help="crude-skal — Skål Australia member portal.")
member_app = typer.Typer(help="Skål members.")
club_app = typer.Typer(help="Skål clubs.")
event_app = typer.Typer(help="Skål events.")
app.add_typer(member_app, name="member")
app.add_typer(club_app, name="club")
app.add_typer(event_app, name="event")
console = Console()

register_claude_command(app)


def _write_config(config_path: Path, config: dict) -> None:
    import tomli_w
    with open(config_path, "wb") as f:
        tomli_w.dump(config, f)


def _get_session(config: dict) -> str:
    """Return a session_id from temp file, config.toml, or auto-login."""
    from crude_skal.client import session_path
    cache = session_path()

    if cache.exists():
        session_id = cache.read_text().strip()
        if session_id:
            return session_id

    skal = _resolve_account(config, "skal", _account())
    session_id = skal.get("session_id", "")
    if session_id:
        cache.write_text(session_id)
        return session_id

    username = skal.get("username")
    password = skal.get("password")
    if username and password:
        from crude_skal.auth import skal_login
        typer.echo("No cached session found — logging in automatically...", err=True)
        try:
            session_id = skal_login(username, password)
        except Exception as e:
            typer.echo(f"Auto-login failed: {e}", err=True)
            raise typer.Exit(1)
        cache.write_text(session_id)
        return session_id

    typer.echo(
        "No cached session and no credentials found. Run `crude-skal login` first.",
        err=True,
    )
    raise typer.Exit(1)


def _make_client(config: dict):
    from crude_skal.client import SkalClient
    session_id = _get_session(config)
    skal = _resolve_account(config, "skal", _account())
    credentials = {
        "username": skal.get("username"),
        "password": skal.get("password"),
    }
    client = SkalClient(session_id, credentials=credentials)
    if not client.verify_session():
        typer.echo("Session expired — logging in automatically...", err=True)
        if not client._try_refresh():
            typer.echo("Error: could not refresh session. Run `crude-skal login` first.", err=True)
            raise typer.Exit(1)
    return client


def _fmt_m2o(value) -> str:
    """Format an Odoo many2one field (returned as [id, name]) to a string."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return str(value[1])
    if value is None or value is False:
        return ""
    return str(value)


@app.command()
def login():
    """Authenticate using credentials from config.toml and cache the session."""
    from crude_skal.auth import skal_login

    config_path = _find_config()
    config = _read_config(config_path)

    skal = _resolve_account(config, "skal", _account())
    username = skal.get("username")
    password = skal.get("password")
    if not username or not password:
        typer.echo("Error: config.toml must contain [skal] username and password.", err=True)
        raise typer.Exit(1)

    typer.echo(f"Logging in as {username} ...")
    try:
        session_id = skal_login(username, password)
    except Exception as e:
        typer.echo(f"Login failed: {e}", err=True)
        raise typer.Exit(1)

    from crude_skal.client import session_path
    cache = session_path()
    cache.write_text(session_id)

    # Write back to config.toml so the session persists across installs, into the
    # selected account's subtable when one is named, else the bare [skal] section.
    section = config.setdefault("skal", {})
    target = section.setdefault(_account(), {}) if _account() else section
    target["session_id"] = session_id
    _write_config(config_path, config)

    typer.echo(f"Login successful. Session cached in {cache}")
    typer.echo(f"Session ID: {session_id[:16]}...")


@member_app.command("list")
def list_(
    name: Optional[str] = typer.Option(None, "--name", help="Filter by name (case-insensitive contains)."),
    city: Optional[str] = typer.Option(None, "--city", help="Filter by city (case-insensitive contains)."),
    club: Optional[int] = typer.Option(None, "--club", help="Filter by club ID (e.g. 330 for Melbourne)."),
    email: Optional[str] = typer.Option(None, "--email", help="Filter by email (exact match)."),
    member_state: Optional[str] = typer.Option(None, "--state", help="Member state (draft/unpaid/done/club_change). Default: excludes 'done'."),
    limit: int = typer.Option(20, "--limit", help="Maximum number of results."),
    offset: int = typer.Option(0, "--offset", help="Number of results to skip."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """List current Australian members.

    With no filters this returns the current member roster (excluding departed).
    Any filter flag narrows the search.
    """
    config_path = _find_config()
    config = _read_config(config_path)
    client = _make_client(config)

    has_filter = any(v is not None for v in (name, city, club, email, member_state))

    try:
        if has_filter:
            domain = [["national_committee_id", "=", 1000]]
            if name is not None:
                domain.append(["name", "ilike", name])
            if city is not None:
                domain.append(["work_city", "ilike", city])
            if club is not None:
                domain.append(["entity_id", "=", club])
            if email is not None:
                domain.append(["work_email", "=", email])
            if member_state is not None:
                domain.append(["state", "=", member_state])
            else:
                domain.append(["state", "not in", ["done"]])
            items = client.search_members(domain, limit=limit, offset=offset)
        else:
            items = client.list_members(limit=limit, offset=offset)
    except Exception as e:
        typer.echo(f"Error fetching members: {e}", err=True)
        raise typer.Exit(1)

    if output_json:
        typer.echo(json.dumps(items, indent=2))
        return

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("ID", style="dim")
    table.add_column("Name")
    table.add_column("Email")
    table.add_column("City")
    table.add_column("Club")
    table.add_column("State")

    for item in items:
        table.add_row(
            _s(item.get("id")),
            _s(item.get("name")),
            _s(item.get("work_email")),
            _s(item.get("work_city")),
            _fmt_m2o(item.get("entity_id")),
            _s(item.get("state")),
        )

    console.print(table)
    typer.echo(f"\n{len(items)} member(s) found.")


@member_app.command("get")
def get(
    member_id: int = typer.Argument(..., help="Member Odoo integer ID (e.g. 184914)."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """Show details of a single member."""
    config_path = _find_config()
    config = _read_config(config_path)
    client = _make_client(config)

    try:
        item = client.get_member(member_id)
    except Exception as e:
        typer.echo(f"Error fetching member {member_id}: {e}", err=True)
        raise typer.Exit(1)

    if output_json:
        typer.echo(json.dumps(item, indent=2))
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Field")
    table.add_column("Value")

    display_fields = [
        ("id", "ID"),
        ("name", "Name"),
        ("first_name", "First Name"),
        ("last_name", "Last Name"),
        ("member_code", "Member Code"),
        ("work_email", "Email"),
        ("work_phone", "Phone"),
        ("work_mobile", "Mobile"),
        ("work_city", "City"),
        ("work_country_id", "Country"),
        ("principal_work_company", "Company"),
        ("principal_work_position", "Position"),
        ("entity_id", "Club"),
        ("national_committee_id", "NC"),
        ("state", "State"),
        ("category_type", "Category"),
        ("gender", "Gender"),
        ("start_date", "Start Date"),
        ("leaving_date", "Leaving Date"),
        ("linkedin_url", "LinkedIn"),
        ("facebook_url", "Facebook"),
        ("twitter_url", "Twitter"),
        ("instagram_url", "Instagram"),
    ]

    for key, label in display_fields:
        value = item.get(key, "")
        if isinstance(value, (list, tuple)) and len(value) == 2:
            value = value[1]
        if value is None or value is False:
            value = ""
        if isinstance(value, (dict, list)):
            value = json.dumps(value)
        value_str = str(value)
        if len(value_str) > 200:
            value_str = value_str[:197] + "..."
        table.add_row(label, value_str)

    console.print(table)


@club_app.command("list")
def list_clubs(
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """List all Australian Skål clubs."""
    config_path = _find_config()
    config = _read_config(config_path)
    client = _make_client(config)

    try:
        items = client.list_clubs()
    except Exception as e:
        typer.echo(f"Error fetching clubs: {e}", err=True)
        raise typer.Exit(1)

    if output_json:
        typer.echo(json.dumps(items, indent=2))
        return

    table = Table(show_header=True, header_style="bold blue")
    table.add_column("ID", style="dim")
    table.add_column("Name")
    table.add_column("Members", justify="right")

    for item in items:
        table.add_row(
            _s(item.get("id")),
            _s(item.get("name")),
            _s(item.get("member_count")),
        )

    console.print(table)
    typer.echo(f"\n{len(items)} club(s) found.")


@event_app.command("list")
def list_events(
    limit: int = typer.Option(20, "--limit", help="Maximum number of events to return."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """List Skål events (most recent first)."""
    config_path = _find_config()
    config = _read_config(config_path)
    client = _make_client(config)

    try:
        items = client.list_events(limit=limit)
    except Exception as e:
        typer.echo(f"Error fetching events: {e}", err=True)
        raise typer.Exit(1)

    if output_json:
        typer.echo(json.dumps(items, indent=2))
        return

    table = Table(show_header=True, header_style="bold yellow")
    table.add_column("ID", style="dim")
    table.add_column("Name")
    table.add_column("Date Begin")
    table.add_column("Location")
    table.add_column("State")

    for item in items:
        table.add_row(
            _s(item.get("id")),
            _s(item.get("name")),
            _s(item.get("date_begin")),
            _s(item.get("location")),
            _s(item.get("state")),
        )

    console.print(table)
    typer.echo(f"\n{len(items)} event(s) found.")


if __name__ == "__main__":
    app()
