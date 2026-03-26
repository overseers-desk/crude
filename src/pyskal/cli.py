"""Typer CLI entry point for pyskal."""

import sys
import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="Python CLI wrapper for the Skål Australia member portal.")
console = Console()


def _find_config() -> Path:
    """Locate config.toml: project root (this file's parents) or CWD."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "config.toml"
        if candidate.exists():
            return candidate
    cwd_candidate = Path.cwd() / "config.toml"
    if cwd_candidate.exists():
        return cwd_candidate
    typer.echo("Error: config.toml not found. Expected in project root or CWD.", err=True)
    raise typer.Exit(1)


def _read_config(config_path: Path) -> dict:
    if sys.version_info >= (3, 11):
        import tomllib
        with open(config_path, "rb") as f:
            return tomllib.load(f)
    else:
        import tomli
        with open(config_path, "rb") as f:
            return tomli.load(f)


def _write_config(config_path: Path, config: dict) -> None:
    import tomli_w
    with open(config_path, "wb") as f:
        tomli_w.dump(config, f)


def _get_session(config: dict) -> str:
    """Return a session_id from temp file, config.toml, or auto-login."""
    from pyskal.client import SESSION_PATH

    if SESSION_PATH.exists():
        session_id = SESSION_PATH.read_text().strip()
        if session_id:
            return session_id

    session_id = config.get("skal", {}).get("session_id", "")
    if session_id:
        SESSION_PATH.write_text(session_id)
        return session_id

    username = config.get("skal", {}).get("username")
    password = config.get("skal", {}).get("password")
    if username and password:
        from pyskal.auth import skal_login
        typer.echo("No cached session found — logging in automatically...", err=True)
        try:
            session_id = skal_login(username, password)
        except Exception as e:
            typer.echo(f"Auto-login failed: {e}", err=True)
            raise typer.Exit(1)
        SESSION_PATH.write_text(session_id)
        return session_id

    typer.echo(
        "No cached session and no credentials found. Run `skal login` first.",
        err=True,
    )
    raise typer.Exit(1)


def _make_client(config: dict):
    from pyskal.client import SkalClient
    session_id = _get_session(config)
    credentials = {
        "username": config.get("skal", {}).get("username"),
        "password": config.get("skal", {}).get("password"),
    }
    client = SkalClient(session_id, credentials=credentials)
    if not client.verify_session():
        typer.echo("Session expired — logging in automatically...", err=True)
        if not client._try_refresh():
            typer.echo("Error: could not refresh session. Run `skal login` first.", err=True)
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
    from pyskal.auth import skal_login
    from pyskal.client import SESSION_PATH

    config_path = _find_config()
    config = _read_config(config_path)

    skal = config.get("skal", {})
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

    SESSION_PATH.write_text(session_id)

    # Write back to config.toml so the session persists across installs
    config["skal"]["session_id"] = session_id
    _write_config(config_path, config)

    typer.echo(f"Login successful. Session cached in {SESSION_PATH}")
    typer.echo(f"Session ID: {session_id[:16]}...")


@app.command()
def members(
    limit: int = typer.Option(100, "--limit", help="Maximum number of members to return."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """List current Australian members (excludes departed)."""
    config_path = _find_config()
    config = _read_config(config_path)
    client = _make_client(config)

    try:
        items = client.list_members(limit=limit)
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
            str(item.get("id", "")),
            item.get("name", ""),
            item.get("work_email", "") or "",
            item.get("work_city", "") or "",
            _fmt_m2o(item.get("entity_id")),
            item.get("state", ""),
        )

    console.print(table)
    typer.echo(f"\n{len(items)} member(s) found.")


@app.command()
def member(
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


@app.command()
def search(
    name: Optional[str] = typer.Option(None, "--name", help="Filter by name (case-insensitive contains)."),
    city: Optional[str] = typer.Option(None, "--city", help="Filter by city (case-insensitive contains)."),
    club: Optional[int] = typer.Option(None, "--club", help="Filter by club ID (e.g. 330 for Melbourne)."),
    email: Optional[str] = typer.Option(None, "--email", help="Filter by email (exact match)."),
    member_state: Optional[str] = typer.Option(None, "--state", help="Member state (draft/unpaid/done/club_change). Default: excludes 'done'."),
    limit: int = typer.Option(20, "--limit", help="Maximum number of results."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """Search Australian members with optional filters."""
    config_path = _find_config()
    config = _read_config(config_path)
    client = _make_client(config)

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

    try:
        items = client.search_members(domain, limit=limit)
    except Exception as e:
        typer.echo(f"Error searching members: {e}", err=True)
        raise typer.Exit(1)

    if output_json:
        typer.echo(json.dumps(items, indent=2))
        return

    table = Table(show_header=True, header_style="bold green")
    table.add_column("ID", style="dim")
    table.add_column("Name")
    table.add_column("Email")
    table.add_column("City")
    table.add_column("Club")
    table.add_column("State")

    for item in items:
        table.add_row(
            str(item.get("id", "")),
            item.get("name", ""),
            item.get("work_email", "") or "",
            item.get("work_city", "") or "",
            _fmt_m2o(item.get("entity_id")),
            item.get("state", ""),
        )

    console.print(table)
    typer.echo(f"\n{len(items)} member(s) found.")


@app.command()
def clubs(
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
            str(item.get("id", "")),
            item.get("name", ""),
            str(item.get("member_count", "")),
        )

    console.print(table)
    typer.echo(f"\n{len(items)} club(s) found.")


@app.command()
def events(
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
            str(item.get("id", "")),
            item.get("name", ""),
            str(item.get("date_begin", "") or ""),
            str(item.get("location", "") or ""),
            item.get("state", ""),
        )

    console.print(table)
    typer.echo(f"\n{len(items)} event(s) found.")


if __name__ == "__main__":
    app()
