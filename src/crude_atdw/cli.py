"""Typer CLI for the ATDW (Australian Tourism Data Warehouse) site: crude-atdw."""

import json
import sys
from typing import Optional

import requests
import typer
from rich.console import Console
from rich.table import Table

from crude_common import asof
from crude_common.claude_command import register_claude_command
from crude_common.config import (
    account,
    find_config,
    read_config,
    resolve_account,
)
from crude_common.output import emit_list
from crude_common.statestore import atomic_write

app = typer.Typer(help="crude-atdw — ATDW (Australian Tourism Data Warehouse) listings.")
listing_app = typer.Typer(help="ATDW listings — list, get, create, update, submit.")
app.add_typer(listing_app, name="listing")
console = Console()

register_claude_command(app)


def _get_token(config: dict) -> str:
    """Return cached token from temp file, or auto-login if credentials are present."""
    from crude_atdw.client import token_path
    cache = token_path()
    if cache.exists():
        token = cache.read_text().strip()
        if token:
            return token
    atdw = resolve_account(config, "atdw", account())
    username = atdw.get("username")
    password = atdw.get("password")
    if username and password:
        from crude_atdw.auth import atdw_login
        typer.echo("No cached token found — logging in automatically...", err=True)
        try:
            token = atdw_login(username, password)
        except Exception as e:
            typer.echo(f"Auto-login failed: {e}", err=True)
            raise typer.Exit(1)
        atomic_write(cache, token)
        return token
    typer.echo(
        "No cached token and no credentials found. Run `crude-atdw login` first.",
        err=True,
    )
    raise typer.Exit(1)


def _make_client(config: dict):
    from crude_atdw.client import ATDWClient
    token = _get_token(config)
    atdw = resolve_account(config, "atdw", account())
    credentials = {
        "username": atdw.get("username"),
        "password": atdw.get("password"),
    }
    return ATDWClient(token, credentials=credentials)


@app.command()
def login():
    """Authenticate using credentials from config.toml and cache the JWT token."""
    from crude_atdw.auth import atdw_login

    config_path = find_config()
    config = read_config(config_path)

    auth = resolve_account(config, "atdw", account())
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

    from crude_atdw.client import token_path
    cache = token_path()
    atomic_write(cache, token)
    typer.echo(f"Login successful. Token cached in {cache}")
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
    config_path = find_config()
    config = read_config(config_path)
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

    # The weakest WORLD_AS_OF boundary in the tool, by design: listings are
    # pure mutable documents with no creation date, and a listing's identity
    # is stable while its body drifts. Nothing is dropped; anything touched
    # after the cutoff is flagged via updatedOn.
    items = asof.bound_records(items, None, "updatedOn", what="listing")

    emit_list(items, [
        ("ID", "id"),
        ("Type", "listingType"),
        ("Slug", "slug"),
        ("Status", "status"),
    ], "listing", output_json)


@listing_app.command("get")
def get(
    listing_id: str = typer.Argument(..., help="Listing ID"),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
):
    """Show details of a single listing, including media count, tags, and services count."""
    config_path = find_config()
    config = read_config(config_path)
    client = _make_client(config)

    try:
        item = client.get_own_listing(listing_id)
    except Exception:
        try:
            item = client.get_published_listing(listing_id)
        except Exception as e:
            typer.echo(f"Error fetching listing {listing_id}: {e}", err=True)
            raise typer.Exit(1)

    # Current state, flagged when touched after the cutoff (never refused:
    # the listing's identity predates its body).
    item = asof.check_record(item, None, "updatedOn", what="listing")

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


@listing_app.command("create")
def create(
    data: Optional[str] = typer.Option(None, "--data", help="Listing object as JSON (or -f / stdin)."),
    file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the created listing."),
):
    """Create a new listing (POST /api/listings).

    The body is a full listing object passed via --data, -f/--file, or stdin.
    ATDW requires at least listingType, category, owningOrganisation, name, and
    physicalAddress; owningOrganisation defaults to your organisation when
    omitted. A new listing starts as a draft and is not distributed until it is
    submitted (`listing submit`).
    """
    config_path = find_config()
    config = read_config(config_path)

    # Resolve the body: --data inline JSON, then -f file, then piped stdin.
    if data is not None:
        raw = data
    elif file is not None:
        with open(file, "r") as f:
            raw = f.read()
    elif not sys.stdin.isatty():
        raw = sys.stdin.read()
    else:
        typer.echo("Error: provide JSON via --data, -f/--file, or stdin.", err=True)
        raise typer.Exit(1)
    try:
        body = json.loads(raw)
    except ValueError as e:
        typer.echo(f"Error: invalid JSON: {e}", err=True)
        raise typer.Exit(1)
    if not isinstance(body, dict):
        typer.echo("Error: JSON body must be an object.", err=True)
        raise typer.Exit(1)

    # A listing belongs to an organisation; default to the configured one.
    from crude_atdw.client import ORG_ID
    body.setdefault("owningOrganisation", ORG_ID)

    missing = [
        f for f in ("listingType", "category", "owningOrganisation", "name", "physicalAddress")
        if not body.get(f)
    ]
    if missing:
        typer.echo(f"Warning: ATDW usually requires these missing fields: {', '.join(missing)}", err=True)

    if not yes:
        typer.confirm(f'Create a new ATDW listing "{body.get("name", "(unnamed)")}"?', abort=True)

    client = _make_client(config)
    try:
        result = client.create_listing(body)
    except requests.exceptions.HTTPError as e:
        typer.echo(f"Error creating listing: {e}", err=True)
        if e.response is not None:
            try:
                msg = e.response.json().get("error", {}).get("message", "")
                if msg:
                    typer.echo(f"ATDW says: {msg}", err=True)
            except Exception:
                pass
        raise typer.Exit(1)
    except Exception as e:
        typer.echo(f"Error creating listing: {e}", err=True)
        raise typer.Exit(1)

    if output_json:
        typer.echo(json.dumps(result, indent=2))
        return
    typer.echo(
        f'Created listing "{result.get("name", "")}" '
        f'(id {result.get("id", "?")}, status {result.get("status", "?")}).'
    )


@listing_app.command("update")
def update(
    listing_id: str = typer.Argument(..., help="Listing ID"),
    field: str = typer.Argument(..., help="Field name to update (e.g. description)"),
    value: str = typer.Argument(..., help="New value for the field"),
):
    """Update a single field on a listing (PATCH)."""
    config_path = find_config()
    config = read_config(config_path)
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
    config_path = find_config()
    config = read_config(config_path)
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
