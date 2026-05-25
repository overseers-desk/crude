"""Typer CLI for the Skål Australia member portal: crude-skal."""

import os
import sys
import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="crude-skal — Skål Australia member portal.")
member_app = typer.Typer(help="Skål members.")
club_app = typer.Typer(help="Skål clubs.")
event_app = typer.Typer(help="Skål events.")
app.add_typer(member_app, name="member")
app.add_typer(club_app, name="club")
app.add_typer(event_app, name="event")
console = Console()

from crude_common import __version__ as _SKILL_VERSION
from crude_common import claude_command as _claude


@app.callback()
def _root(ctx: typer.Context):
    if ctx.invoked_subcommand != "install-claude-command":
        nudge = _claude.registration_status(_SKILL_VERSION, "crude-skal")
        if nudge:
            typer.echo(nudge, err=True)


@app.command("install-claude-command")
def install_claude_command():
    """Install or update the crude skill for Claude Code."""
    _claude.run_install(_SKILL_VERSION, "crude-skal")


def _find_config() -> Path:
    """Locate config.toml: ~/.config/crude/ (XDG), then project root, then CWD."""
    xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    xdg_candidate = Path(xdg) / "crude" / "config.toml"
    if xdg_candidate.exists():
        return xdg_candidate
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "config.toml"
        if candidate.exists():
            return candidate
    cwd_candidate = Path.cwd() / "config.toml"
    if cwd_candidate.exists():
        return cwd_candidate
    typer.echo(
        "Error: config.toml not found. Expected at ~/.config/crude/config.toml, project root, or CWD.",
        err=True,
    )
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
    from crude_skal.client import SESSION_PATH

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
        from crude_skal.auth import skal_login
        typer.echo("No cached session found — logging in automatically...", err=True)
        try:
            session_id = skal_login(username, password)
        except Exception as e:
            typer.echo(f"Auto-login failed: {e}", err=True)
            raise typer.Exit(1)
        SESSION_PATH.write_text(session_id)
        return session_id

    typer.echo(
        "No cached session and no credentials found. Run `crude-skal login` first.",
        err=True,
    )
    raise typer.Exit(1)


def _make_client(config: dict):
    from crude_skal.client import SkalClient
    session_id = _get_session(config)
    credentials = {
        "username": config.get("skal", {}).get("username"),
        "password": config.get("skal", {}).get("password"),
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


def _s(value) -> str:
    """Coerce a field value to str, treating None/False as empty."""
    if value is None or value is False:
        return ""
    return str(value)


@app.command()
def login():
    """Authenticate using credentials from config.toml and cache the session."""
    from crude_skal.auth import skal_login
    from crude_skal.client import SESSION_PATH

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
    config.setdefault("skal", {})["session_id"] = session_id
    _write_config(config_path, config)

    typer.echo(f"Login successful. Session cached in {SESSION_PATH}")
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
