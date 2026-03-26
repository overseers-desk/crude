"""Typer CLI entry point for pyatdw."""

import sys
import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="Python CLI wrapper for the ATDW REST API.")
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


def _get_token(config: dict) -> str:
    """Return cached token from temp file, or auto-login if credentials are present."""
    from pyatdw.client import TOKEN_PATH
    if TOKEN_PATH.exists():
        token = TOKEN_PATH.read_text().strip()
        if token:
            return token
    username = config.get("atdw", {}).get("username")
    password = config.get("atdw", {}).get("password")
    if username and password:
        from pyatdw.auth import atdw_login
        typer.echo("No cached token found — logging in automatically...", err=True)
        try:
            token = atdw_login(username, password)
        except Exception as e:
            typer.echo(f"Auto-login failed: {e}", err=True)
            raise typer.Exit(1)
        TOKEN_PATH.write_text(token)
        return token
    typer.echo(
        "No cached token and no credentials found. Run `atdw login` first.",
        err=True,
    )
    raise typer.Exit(1)


def _make_client(config: dict):
    from pyatdw.client import ATDWClient
    token = _get_token(config)
    credentials = {
        "username": config.get("atdw", {}).get("username"),
        "password": config.get("atdw", {}).get("password"),
    }
    return ATDWClient(token, credentials=credentials)


@app.command()
def login():
    """Authenticate using credentials from config.toml and cache the JWT token."""
    from pyatdw.auth import atdw_login

    config_path = _find_config()
    config = _read_config(config_path)

    auth = config.get("atdw", {})
    username = auth.get("username")
    password = auth.get("password")
    if not username or not password:
        typer.echo("Error: config.toml must contain [atdw] username and password.", err=True)
        raise typer.Exit(1)

    typer.echo(f"Logging in as {username} ...")
    try:
        token = atdw_login(username, password)
    except Exception as e:
        typer.echo(f"Login failed: {e}", err=True)
        raise typer.Exit(1)

    from pyatdw.client import TOKEN_PATH
    TOKEN_PATH.write_text(token)
    typer.echo(f"Login successful. Token cached in {TOKEN_PATH}")
    typer.echo(f"Token (first 40 chars): {token[:40]}...")


@app.command()
def listings(
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """List all listings for the organisation."""
    config_path = _find_config()
    config = _read_config(config_path)
    client = _make_client(config)

    try:
        items = client.list_listings()
    except Exception as e:
        typer.echo(f"Error fetching listings: {e}", err=True)
        raise typer.Exit(1)

    if output_json:
        typer.echo(json.dumps(items, indent=2))
        return

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("ID", style="dim")
    table.add_column("Type")
    table.add_column("Slug")
    table.add_column("Status")

    for item in items:
        table.add_row(
            item.get("id", ""),
            item.get("listingType", ""),
            item.get("slug", ""),
            item.get("status", ""),
        )

    console.print(table)
    typer.echo(f"\n{len(items)} listing(s) found.")


@app.command()
def listing(
    listing_id: str = typer.Argument(..., help="Listing ID"),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """Show details of a single listing, including media count, tags, and services count."""
    config_path = _find_config()
    config = _read_config(config_path)
    client = _make_client(config)

    try:
        item = client.get_own_listing(listing_id)
    except Exception:
        try:
            item = client.get_published_listing(listing_id)
        except Exception as e:
            typer.echo(f"Error fetching listing {listing_id}: {e}", err=True)
            raise typer.Exit(1)

    if output_json:
        typer.echo(json.dumps(item, indent=2))
        return

    # Fetch sub-resources for enriched display
    media_count = 0
    services_count = 0
    tag_ids: list = []

    try:
        media = client.list_media(listing_id)
        media_count = len(media) if isinstance(media, list) else 0
    except Exception:
        pass

    try:
        services = client.list_services(listing_id)
        services_count = len(services) if isinstance(services, list) else 0
    except Exception:
        pass

    try:
        tags = client.list_tags(listing_id)
        if isinstance(tags, list):
            tag_ids = [t.get("id", str(t)) for t in tags]
    except Exception:
        pass

    # Print key fields in a table
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Field")
    table.add_column("Value")

    display_fields = [
        ("id", "ID"),
        ("listingType", "Type"),
        ("category", "Category"),
        ("slug", "Slug"),
        ("status", "Status"),
        ("name", "Name"),
        ("productNumber", "Product Number"),
        ("description", "Description"),
        ("shortDescription", "Short Description"),
        ("publishedOn", "Published On"),
        ("updatedOn", "Updated On"),
    ]

    for key, label in display_fields:
        value = item.get(key, "")
        if value is None:
            value = ""
        if isinstance(value, (dict, list)):
            value = json.dumps(value, indent=2)
        value_str = str(value)
        if len(value_str) > 200:
            value_str = value_str[:197] + "..."
        table.add_row(label, value_str)

    # Enriched rows
    table.add_row("Media Count", str(media_count))
    table.add_row("Services Count", str(services_count))
    tag_display = ", ".join(tag_ids) if tag_ids else "(none)"
    table.add_row("Tags", tag_display)

    console.print(table)


@app.command()
def edit(
    listing_id: str = typer.Argument(..., help="Listing ID"),
    field: str = typer.Argument(..., help="Field name to update (e.g. description)"),
    value: str = typer.Argument(..., help="New value for the field"),
):
    """PATCH a single field on a listing."""
    config_path = _find_config()
    config = _read_config(config_path)
    client = _make_client(config)

    try:
        result = client.patch_listing(listing_id, {field: value})
    except Exception as e:
        typer.echo(f"Error patching listing {listing_id}: {e}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Successfully patched listing {listing_id}.")
    typer.echo(f"  {field} = {str(result.get(field, ''))[:200]}")


@app.command()
def search(
    listing_type: Optional[str] = typer.Option(None, "--type", help="Filter by listingType (e.g. tour, attraction)."),
    city: Optional[str] = typer.Option(None, "--city", help="Filter by city/suburb."),
    state: Optional[str] = typer.Option(None, "--state", help="Filter by state."),
    status: Optional[str] = typer.Option(None, "--status", help="Filter by status (default excludes INACTIVE)."),
    name: Optional[str] = typer.Option(None, "--name", help="Filter by name (regex/like match)."),
    limit: int = typer.Option(20, "--limit", help="Maximum number of results to return."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """Search across all visible listings using optional filters."""
    config_path = _find_config()
    config = _read_config(config_path)
    client = _make_client(config)

    # Build LoopBack where clauses from provided flags
    where_clauses = []

    if listing_type is not None:
        where_clauses.append({"listingType": listing_type})

    if city is not None:
        where_clauses.append({"physicalAddress.city_suburb": city})

    if state is not None:
        where_clauses.append({"physicalAddress.state": state})

    if name is not None:
        where_clauses.append({"name": {"regexp": f"/{name}/i"}})

    # Status: if --status is explicitly given, filter to that status;
    # otherwise exclude INACTIVE by default.
    if status is not None:
        where_clauses.append({"status": status})
    else:
        where_clauses.append({"status": {"neq": "INACTIVE"}})

    # If no clauses at all (shouldn't happen given default status clause), pass an empty and
    if not where_clauses:
        where_clauses.append({"status": {"neq": "INACTIVE"}})

    try:
        items = client.search_listings(where_clauses, limit=limit)
    except Exception as e:
        typer.echo(f"Error searching listings: {e}", err=True)
        raise typer.Exit(1)

    if output_json:
        typer.echo(json.dumps(items, indent=2))
        return

    table = Table(show_header=True, header_style="bold green")
    table.add_column("ID", style="dim")
    table.add_column("Type")
    table.add_column("Slug")
    table.add_column("Status")

    for item in items:
        table.add_row(
            item.get("id", ""),
            item.get("listingType", ""),
            item.get("slug", ""),
            item.get("status", ""),
        )

    console.print(table)
    typer.echo(f"\n{len(items)} listing(s) found.")


if __name__ == "__main__":
    app()
