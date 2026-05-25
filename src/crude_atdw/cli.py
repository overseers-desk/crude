"""Typer CLI for the ATDW (Australian Tourism Data Warehouse) site: crude-atdw."""

import os
import sys
import json
from pathlib import Path
from typing import Optional

import requests
import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="crude-atdw — ATDW (Australian Tourism Data Warehouse) listings.")
listing_app = typer.Typer(help="ATDW listings.")
app.add_typer(listing_app, name="listing")
console = Console()


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


def _get_token(config: dict) -> str:
    """Return cached token from temp file, or auto-login if credentials are present."""
    from crude_atdw.client import TOKEN_PATH
    if TOKEN_PATH.exists():
        token = TOKEN_PATH.read_text().strip()
        if token:
            return token
    username = config.get("atdw", {}).get("username")
    password = config.get("atdw", {}).get("password")
    if username and password:
        from crude_atdw.auth import atdw_login
        typer.echo("No cached token found — logging in automatically...", err=True)
        try:
            token = atdw_login(username, password)
        except Exception as e:
            typer.echo(f"Auto-login failed: {e}", err=True)
            raise typer.Exit(1)
        TOKEN_PATH.write_text(token)
        return token
    typer.echo(
        "No cached token and no credentials found. Run `crude-atdw login` first.",
        err=True,
    )
    raise typer.Exit(1)


def _make_client(config: dict):
    from crude_atdw.client import ATDWClient
    token = _get_token(config)
    credentials = {
        "username": config.get("atdw", {}).get("username"),
        "password": config.get("atdw", {}).get("password"),
    }
    return ATDWClient(token, credentials=credentials)


@app.command()
def login():
    """Authenticate using credentials from config.toml and cache the JWT token."""
    from crude_atdw.auth import atdw_login

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

    from crude_atdw.client import TOKEN_PATH
    TOKEN_PATH.write_text(token)
    typer.echo(f"Login successful. Token cached in {TOKEN_PATH}")
    typer.echo(f"Token (first 40 chars): {token[:40]}...")


@listing_app.command("list")
def list_(
    scope: str = typer.Option("own", "--scope", help="own (your organisation) or all (every visible listing)."),
    listing_type: Optional[str] = typer.Option(None, "--type", help="Filter by listingType (e.g. tour, attraction)."),
    city: Optional[str] = typer.Option(None, "--city", help="Filter by city/suburb."),
    state: Optional[str] = typer.Option(None, "--state", help="Filter by state."),
    status: Optional[str] = typer.Option(None, "--status", help="Filter by status (default excludes INACTIVE)."),
    name: Optional[str] = typer.Option(None, "--name", help="Filter by name (regex/like match)."),
    limit: int = typer.Option(20, "--limit", help="Maximum number of results to return."),
    offset: int = typer.Option(0, "--offset", help="Number of results to skip."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """List listings.

    With no filters this returns your organisation's own listings. Any filter
    flag, or --scope all, switches to the all-visible search endpoint.
    """
    config_path = _find_config()
    config = _read_config(config_path)
    client = _make_client(config)

    has_filter = any(v is not None for v in (listing_type, city, state, status, name))

    try:
        if has_filter or scope == "all":
            where_clauses = []
            if listing_type is not None:
                where_clauses.append({"listingType": listing_type})
            if city is not None:
                where_clauses.append({"physicalAddress.city_suburb": city})
            if state is not None:
                where_clauses.append({"physicalAddress.state": state})
            if name is not None:
                where_clauses.append({"name": {"regexp": f"/{name}/i"}})
            if status is not None:
                where_clauses.append({"status": status})
            else:
                where_clauses.append({"status": {"neq": "INACTIVE"}})
            items = client.search_listings(where_clauses, limit=limit, skip=offset)
        else:
            items = client.list_listings(limit=limit, skip=offset)
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


@listing_app.command("get")
def get(
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


@listing_app.command("update")
def update(
    listing_id: str = typer.Argument(..., help="Listing ID"),
    field: str = typer.Argument(..., help="Field name to update (e.g. description)"),
    value: str = typer.Argument(..., help="New value for the field"),
):
    """Update a single field on a listing (PATCH)."""
    config_path = _find_config()
    config = _read_config(config_path)
    client = _make_client(config)

    # If value looks like JSON (array or object), parse it so the API
    # receives the correct type instead of a raw string.
    parsed_value: object = value
    if value.lstrip().startswith(("[", "{")):
        try:
            parsed_value = json.loads(value)
        except json.JSONDecodeError:
            pass  # fall through — send as string

    try:
        result = client.patch_listing(listing_id, {field: parsed_value})
    except Exception as e:
        typer.echo(f"Error patching listing {listing_id}: {e}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Successfully patched listing {listing_id}.")
    typer.echo(f"  {field} = {str(result.get(field, ''))[:200]}")


@listing_app.command("submit")
def submit(
    listing_id: str = typer.Argument(..., help="Listing ID to submit for review"),
):
    """Submit a draft listing for ATDW review (DRAFTINPROG -> ACTIVE)."""
    config_path = _find_config()
    config = _read_config(config_path)
    client = _make_client(config)

    # Fetch current status so we can give a meaningful message
    try:
        item = client.get_own_listing(listing_id)
    except Exception as e:
        typer.echo(f"Error fetching listing {listing_id}: {e}", err=True)
        raise typer.Exit(1)

    status = item.get("status", "")
    name = item.get("name", listing_id)

    if status == "ACTIVE":
        typer.echo(f'Listing "{name}" is already ACTIVE — nothing to submit.')
        return

    if status not in ("DRAFT", "DRAFTINPROG"):
        typer.echo(
            f'Listing "{name}" has status {status} — only DRAFT/DRAFTINPROG listings can be submitted.',
            err=True,
        )
        raise typer.Exit(1)

    try:
        result = client.submit(listing_id)
    except requests.exceptions.HTTPError as e:
        typer.echo(f"Error submitting listing {listing_id}: {e}", err=True)
        # Surface the API validation errors if present
        if e.response is not None:
            try:
                body = e.response.json()
                error_msg = body.get("error", {}).get("message", "")
                if error_msg:
                    typer.echo(f"ATDW says: {error_msg}", err=True)
            except Exception:
                pass
        raise typer.Exit(1)
    except Exception as e:
        typer.echo(f"Error submitting listing {listing_id}: {e}", err=True)
        raise typer.Exit(1)

    new_status = result.get("status", "(unknown)")
    typer.echo(f'Submitted "{name}" for review. New status: {new_status}')


if __name__ == "__main__":
    app()
