"""Typer CLI for the Skål Australia member portal: crude-skal."""

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from crude_common.claude_command import register_claude_command
from crude_common.config import (
    account,
    find_config,
    read_config,
    resolve_account,
    s,
)
from crude_common.output import emit_list
from crude_common.statestore import atomic_write

app = typer.Typer(help="crude-skal — Skål Australia member portal.")
member_app = typer.Typer(help="Skål members.")
club_app = typer.Typer(help="Skål clubs.")
event_app = typer.Typer(help="Skål events.")
benefit_app = typer.Typer(help="Skål member benefits (global).")
app.add_typer(member_app, name="member")
app.add_typer(club_app, name="club")
app.add_typer(event_app, name="event")
app.add_typer(benefit_app, name="benefit")
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

    skal = resolve_account(config, "skal", account())
    session_id = skal.get("session_id", "")
    if session_id:
        atomic_write(cache, session_id)
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
        atomic_write(cache, session_id)
        return session_id

    typer.echo(
        "No cached session and no credentials found. Run `crude-skal login` first.",
        err=True,
    )
    raise typer.Exit(1)


def _make_client(config: dict):
    from crude_skal.client import SkalClient
    session_id = _get_session(config)
    skal = resolve_account(config, "skal", account())
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

    config_path = find_config()
    config = read_config(config_path)

    skal = resolve_account(config, "skal", account())
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
    atomic_write(cache, session_id)

    # Write back to config.toml so the session persists across installs, into the
    # selected account's subtable when one is named, else the bare [skal] section.
    section = config.setdefault("skal", {})
    target = section.setdefault(account(), {}) if account() else section
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
    config_path = find_config()
    config = read_config(config_path)
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

    emit_list(items, [
        ("ID", "id"),
        ("Name", "name"),
        ("Email", "work_email"),
        ("City", "work_city"),
        ("Club", lambda it: _fmt_m2o(it.get("entity_id"))),
        ("State", "state"),
    ], "member", output_json)


@member_app.command("get")
def get(
    member_id: int = typer.Argument(..., help="Member Odoo integer ID (e.g. 184914)."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """Show details of a single member."""
    config_path = find_config()
    config = read_config(config_path)
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
    config_path = find_config()
    config = read_config(config_path)
    client = _make_client(config)

    try:
        items = client.list_clubs()
    except Exception as e:
        typer.echo(f"Error fetching clubs: {e}", err=True)
        raise typer.Exit(1)

    emit_list(items, [
        ("ID", "id"),
        ("Name", "name"),
        ("Members", "member_count"),
    ], "club", output_json, header_style="bold blue")


@event_app.command("list")
def list_events(
    limit: int = typer.Option(20, "--limit", help="Maximum number of events to return."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """List Skål events (most recent first)."""
    config_path = find_config()
    config = read_config(config_path)
    client = _make_client(config)

    try:
        items = client.list_events(limit=limit)
    except Exception as e:
        typer.echo(f"Error fetching events: {e}", err=True)
        raise typer.Exit(1)

    emit_list(items, [
        ("ID", "id"),
        ("Name", "name"),
        ("Date Begin", "date_begin"),
        ("Location", "location"),
        ("State", "state"),
    ], "event", output_json, header_style="bold yellow")


@benefit_app.command("list")
def list_benefits(
    limit: int = typer.Option(50, "--limit", help="Maximum number of benefits to return."),
    offset: int = typer.Option(0, "--offset", help="Number of benefits to skip."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """List Skål International member benefits (global, across all clubs).

    These are the worldwide benefits in the skal.benefit register; Australian
    clubs publish their own member offers on a website page, not here.
    """
    config_path = find_config()
    config = read_config(config_path)
    client = _make_client(config)

    try:
        items = client.list_benefits(limit=limit, offset=offset)
    except Exception as e:
        typer.echo(f"Error fetching benefits: {e}", err=True)
        raise typer.Exit(1)

    emit_list(items, [
        ("ID", "id"),
        ("Title", "name"),
        ("Activity", lambda it: _fmt_m2o(it.get("activity_id"))),
        ("Club", lambda it: _fmt_m2o(it.get("entity_id"))),
        ("Country", lambda it: _fmt_m2o(it.get("country_id"))),
        ("Website", "website"),
    ], "benefit", output_json, header_style="bold green")


@benefit_app.command("get")
def get_benefit(
    benefit_id: int = typer.Argument(..., help="Benefit Odoo integer ID (e.g. 178)."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """Show details of a single member benefit."""
    config_path = find_config()
    config = read_config(config_path)
    client = _make_client(config)

    try:
        item = client.get_benefit(benefit_id)
    except Exception as e:
        typer.echo(f"Error fetching benefit {benefit_id}: {e}", err=True)
        raise typer.Exit(1)

    if output_json:
        typer.echo(json.dumps(item, indent=2))
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Field")
    table.add_column("Value")

    display_fields = [
        ("id", "ID"),
        ("name", "Title"),
        ("description", "Description"),
        ("activity_id", "Activity"),
        ("entity_id", "Club"),
        ("country_id", "Country"),
        ("website", "Website"),
        ("start_date", "Start Date"),
        ("end_date", "End Date"),
        ("active", "Active"),
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


if __name__ == "__main__":
    app()
