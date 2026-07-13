"""Typer CLI for the Deputy workforce-management API: crude-deputy.

Deputy's Resource API is uniform across every object, so the surface is split in
two: curated sub-apps (employee, roster, area, timesheet, leave) render friendly
columns for the high-value objects, and a generic ``resource <Object> <verb>``
sub-app reaches every object Deputy exposes, including ones added after this code
was written. ``--json`` on any command prints the complete raw structure.
"""

import json
import sys
from typing import List, Optional

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
    s,
)
from crude_common.output import emit_record

app = typer.Typer(help="crude-deputy — Deputy rostering, timesheets, leave, employees.")
employee_app = typer.Typer(help="Deputy employees.")
roster_app = typer.Typer(help="Deputy rosters (shifts).")
area_app = typer.Typer(help="Deputy areas (operational units).")
timesheet_app = typer.Typer(help="Deputy timesheets.")
leave_app = typer.Typer(help="Deputy leave.")
resource_app = typer.Typer(help="Any Deputy resource object, generically.")
app.add_typer(employee_app, name="employee")
app.add_typer(roster_app, name="roster")
app.add_typer(area_app, name="area")
app.add_typer(timesheet_app, name="timesheet")
app.add_typer(leave_app, name="leave")
app.add_typer(resource_app, name="resource")
console = Console()

register_claude_command(app)

# Deputy QUERY operators, mapped from the verbs accepted on --where.
_OPERATORS = {"eq", "ne", "gt", "ge", "lt", "le", "lk", "nk", "in", "nn", "is", "ns"}


def _make_client(config: dict):
    from crude_deputy.client import DeputyClient
    deputy = resolve_account(config, "deputy", account())
    token = deputy.get("deputy_api_token")
    install = deputy.get("deputy_install")
    geo = deputy.get("deputy_geo")
    if not (token and install and geo):
        typer.echo(
            "Error: config.toml must contain [deputy] deputy_api_token, "
            "deputy_install, deputy_geo.",
            err=True,
        )
        raise typer.Exit(1)
    return DeputyClient(token, install, geo)


# ----------------------------------------------------------------------
# Rendering helpers
# ----------------------------------------------------------------------


def _render_rows(rows: list, columns: Optional[List[str]] = None) -> None:
    """Print a list of records as a table.

    Curated callers pass fixed ``columns``. For arbitrary objects the columns are
    derived from the first row: ``Id`` first, then up to six scalar fields in the
    order Deputy returns them. Nested values are dropped from the table; --json
    shows them.
    """
    if not rows:
        return
    if columns is None:
        first = rows[0]
        scalar = [
            k for k, v in first.items()
            if not isinstance(v, (dict, list)) and k != "Id"
        ]
        columns = (["Id"] if "Id" in first else []) + scalar[:6]
    table = Table(show_header=True, header_style="bold magenta")
    for col in columns:
        table.add_column(col, style="dim" if col == "Id" else None)
    for row in rows:
        cells = []
        for col in columns:
            value = row.get(col)
            cell = "(object)" if isinstance(value, dict) else (
                f"{len(value)} item(s)" if isinstance(value, list) else s(value)
            )
            if len(cell) > 60:
                cell = cell[:57] + "..."
            cells.append(cell)
        table.add_row(*cells)
    console.print(table)


def _emit(items, output_json: bool, columns: Optional[List[str]] = None) -> None:
    """Render a list result: raw JSON, or a table plus a count line."""
    if output_json:
        typer.echo(json.dumps(items, indent=2))
        return
    _render_rows(items, columns=columns)
    typer.echo(f"\n{len(items)} row(s) found.")


# ----------------------------------------------------------------------
# QUERY / write input helpers
# ----------------------------------------------------------------------


def _coerce(value: str):
    """Coerce a --where value: digits to int, true/false to bool, else string."""
    if value.lstrip("-").isdigit():
        return int(value)
    low = value.lower()
    if low in ("true", "false"):
        return low == "true"
    return value


def _parse_where(clauses: List[str]) -> dict:
    """Build a Deputy ``search`` block from repeated "Field op value" tokens.

    Filters are auto-numbered (f1, f2, ...) and AND-combined. For ``in``/``nn``
    the value is comma-split into a list.
    """
    search = {}
    for i, clause in enumerate(clauses, start=1):
        parts = clause.split(None, 2)
        if len(parts) != 3:
            typer.echo(
                f"Error: --where must be 'Field op value', got: {clause!r}", err=True
            )
            raise typer.Exit(1)
        field, op, raw = parts
        if op not in _OPERATORS:
            typer.echo(
                f"Error: unknown operator {op!r}; one of {sorted(_OPERATORS)}.", err=True
            )
            raise typer.Exit(1)
        if op in ("in", "nn"):
            data = [_coerce(v.strip()) for v in raw.split(",")]
        else:
            data = _coerce(raw)
        search[f"f{i}"] = {"field": field, "type": op, "data": data}
    return search


def _parse_sort(sort: Optional[str]) -> Optional[dict]:
    """Build a sort block from "Field:asc|desc" (direction optional, defaults asc)."""
    if not sort:
        return None
    field, _, direction = sort.partition(":")
    return {field: (direction or "asc").lower()}


def _read_data(data: Optional[str], file: Optional[str]) -> dict:
    """Resolve write input: --data inline JSON, then -f file, then stdin."""
    if data is not None:
        raw = data
    elif file is not None:
        with open(file, "r") as f:
            raw = f.read()
    elif not sys.stdin.isatty():
        raw = sys.stdin.read()
    else:
        typer.echo(
            "Error: provide JSON via --data, -f/--file, or stdin.", err=True
        )
        raise typer.Exit(1)
    try:
        parsed = json.loads(raw)
    except ValueError as e:
        typer.echo(f"Error: invalid JSON: {e}", err=True)
        raise typer.Exit(1)
    if not isinstance(parsed, dict):
        typer.echo("Error: JSON body must be an object.", err=True)
        raise typer.Exit(1)
    return parsed


# ----------------------------------------------------------------------
# Top-level: me
# ----------------------------------------------------------------------


@app.command("me")
def me(output_json: bool = typer.Option(False, "--json", help="Print raw JSON.")):
    """Show the current token owner."""
    client = _make_client(read_config(find_config()))
    try:
        item = client.me()
    except Exception as e:
        typer.echo(f"Error fetching current user: {e}", err=True)
        raise typer.Exit(1)
    emit_record(item, output_json)


# ----------------------------------------------------------------------
# Curated sub-apps
# ----------------------------------------------------------------------


def _curated_list(obj, search, sort, fetch_all, limit, columns, output_json, what):
    client = _make_client(read_config(find_config()))
    try:
        if fetch_all:
            items = client.paginate_query(obj, search=search, sort=sort)
        else:
            items = client.query_resource(obj, search=search, sort=sort, max_=limit)
    except Exception as e:
        typer.echo(f"Error fetching {what}: {e}", err=True)
        raise typer.Exit(1)
    # QUERY already carries the server-side `Created le` bound; this is the
    # belt-and-braces drop plus the Modified>bound flag. The bound acts on the
    # audit fields, never the business Date: a roster dated next week but
    # entered before the cutoff is correctly visible.
    items = asof.bound_records(items, "Created", "Modified", what=what)
    _emit(items, output_json, columns=columns)


def _curated_get(obj, id, output_json, what):
    client = _make_client(read_config(find_config()))
    try:
        item = client.get_resource(obj, id)
    except Exception as e:
        typer.echo(f"Error fetching {what} {id}: {e}", err=True)
        raise typer.Exit(1)
    item = asof.check_record(item, "Created", "Modified", what=what)
    emit_record(item, output_json)


@employee_app.command("list")
def employee_list(
    limit: int = typer.Option(50, "--limit", help="Maximum number of results."),
    fetch_all: bool = typer.Option(False, "--all", help="Fetch all pages."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """List employees."""
    _curated_list(
        "Employee", None, None, fetch_all, limit,
        ["Id", "DisplayName", "FirstName", "LastName", "Active"], output_json, "employees",
    )


@employee_app.command("get")
def employee_get(
    id: str = typer.Argument(..., help="Employee Id."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Show a single employee."""
    _curated_get("Employee", id, output_json, "employee")


@roster_app.command("list")
def roster_list(
    from_: Optional[str] = typer.Option(None, "--from", help="On or after this date (YYYY-MM-DD)."),
    to: Optional[str] = typer.Option(None, "--to", help="On or before this date (YYYY-MM-DD)."),
    area: Optional[int] = typer.Option(None, "--area", help="OperationalUnit Id to filter by."),
    employee: Optional[int] = typer.Option(None, "--employee", help="Employee Id to filter by."),
    limit: int = typer.Option(100, "--limit", help="Maximum number of results."),
    fetch_all: bool = typer.Option(False, "--all", help="Fetch all pages."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """List rosters (shifts)."""
    clauses = []
    if from_:
        clauses.append(f"Date ge {from_}")
    if to:
        clauses.append(f"Date le {to}")
    if area is not None:
        clauses.append(f"OperationalUnit eq {area}")
    if employee is not None:
        clauses.append(f"Employee eq {employee}")
    search = _parse_where(clauses) if clauses else None
    _curated_list(
        "Roster", search, {"Date": "asc"}, fetch_all, limit,
        ["Id", "Date", "StartTime", "EndTime", "OperationalUnit", "Employee"],
        output_json, "rosters",
    )


@roster_app.command("get")
def roster_get(
    id: str = typer.Argument(..., help="Roster Id."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Show a single roster."""
    _curated_get("Roster", id, output_json, "roster")


@area_app.command("list")
def area_list(
    fetch_all: bool = typer.Option(False, "--all", help="Fetch all pages."),
    limit: int = typer.Option(100, "--limit", help="Maximum number of results."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """List areas (operational units)."""
    _curated_list(
        "OperationalUnit", None, None, fetch_all, limit,
        ["Id", "OperationalUnitName", "CompanyName", "Active"], output_json, "areas",
    )


@area_app.command("get")
def area_get(
    id: str = typer.Argument(..., help="OperationalUnit Id."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Show a single area (operational unit)."""
    _curated_get("OperationalUnit", id, output_json, "area")


@timesheet_app.command("list")
def timesheet_list(
    from_: Optional[str] = typer.Option(None, "--from", help="On or after this date (YYYY-MM-DD)."),
    to: Optional[str] = typer.Option(None, "--to", help="On or before this date (YYYY-MM-DD)."),
    employee: Optional[int] = typer.Option(None, "--employee", help="Employee Id to filter by."),
    limit: int = typer.Option(100, "--limit", help="Maximum number of results."),
    fetch_all: bool = typer.Option(False, "--all", help="Fetch all pages."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """List timesheets."""
    clauses = []
    if from_:
        clauses.append(f"Date ge {from_}")
    if to:
        clauses.append(f"Date le {to}")
    if employee is not None:
        clauses.append(f"Employee eq {employee}")
    search = _parse_where(clauses) if clauses else None
    _curated_list(
        "Timesheet", search, {"Date": "asc"}, fetch_all, limit,
        ["Id", "Date", "Employee", "StartTime", "EndTime", "TotalTime"],
        output_json, "timesheets",
    )


@timesheet_app.command("get")
def timesheet_get(
    id: str = typer.Argument(..., help="Timesheet Id."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Show a single timesheet."""
    _curated_get("Timesheet", id, output_json, "timesheet")


@leave_app.command("list")
def leave_list(
    employee: Optional[int] = typer.Option(None, "--employee", help="Employee Id to filter by."),
    limit: int = typer.Option(100, "--limit", help="Maximum number of results."),
    fetch_all: bool = typer.Option(False, "--all", help="Fetch all pages."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """List leave records."""
    search = _parse_where([f"Employee eq {employee}"]) if employee is not None else None
    _curated_list(
        "Leave", search, None, fetch_all, limit,
        ["Id", "Employee", "DateStart", "DateEnd", "Status", "Comment"],
        output_json, "leave",
    )


@leave_app.command("get")
def leave_get(
    id: str = typer.Argument(..., help="Leave Id."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Show a single leave record."""
    _curated_get("Leave", id, output_json, "leave")


# ----------------------------------------------------------------------
# Generic resource sub-app
# ----------------------------------------------------------------------


@resource_app.command("list")
def resource_list(
    obj: str = typer.Argument(..., help="Deputy object name, e.g. Employee, Roster, Memo."),
    limit: int = typer.Option(50, "--limit", help="Maximum number of results."),
    start: int = typer.Option(0, "--start", help="Records to skip."),
    fetch_all: bool = typer.Option(False, "--all", help="Fetch all pages."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """List records of any resource object."""
    client = _make_client(read_config(find_config()))
    try:
        if fetch_all:
            items = client.paginate_list(obj)
        else:
            items = client.list_resource(obj, start=start, max_=limit)
    except Exception as e:
        typer.echo(f"Error listing {obj}: {e}", err=True)
        raise typer.Exit(1)
    # The plain GET list takes no query clauses, so the bound is enforced
    # entirely client-side on the returned Created/Modified audit fields.
    items = asof.bound_records(items, "Created", "Modified", what=obj)
    _emit(items, output_json)


@resource_app.command("get")
def resource_get(
    obj: str = typer.Argument(..., help="Deputy object name."),
    id: str = typer.Argument(..., help="Record Id."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Show a single record of any resource object."""
    client = _make_client(read_config(find_config()))
    try:
        item = client.get_resource(obj, id)
    except Exception as e:
        typer.echo(f"Error fetching {obj} {id}: {e}", err=True)
        raise typer.Exit(1)
    item = asof.check_record(item, "Created", "Modified", what=obj)
    emit_record(item, output_json)


@resource_app.command("query")
def resource_query(
    obj: str = typer.Argument(..., help="Deputy object name."),
    where: List[str] = typer.Option(
        [], "--where", help="Filter 'Field op value' (repeatable, AND-combined)."
    ),
    sort: Optional[str] = typer.Option(None, "--sort", help="Sort 'Field:asc|desc'."),
    join: List[str] = typer.Option([], "--join", help="Related object to include (repeatable)."),
    json_query: Optional[str] = typer.Option(
        None, "--json-query", help="Full Deputy QUERY body; overrides --where/--sort/--join."
    ),
    start: int = typer.Option(0, "--start", help="Records to skip."),
    max_: int = typer.Option(500, "--max", help="Maximum records per page."),
    fetch_all: bool = typer.Option(False, "--all", help="Fetch all pages."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Search any resource object with Deputy's QUERY operators."""
    client = _make_client(read_config(find_config()))
    if json_query is not None:
        try:
            body = json.loads(json_query)
        except ValueError as e:
            typer.echo(f"Error: invalid --json-query JSON: {e}", err=True)
            raise typer.Exit(1)
        search = body.get("search")
        sort_block = body.get("sort")
        join_list = body.get("join")
    else:
        search = _parse_where(where) if where else None
        sort_block = _parse_sort(sort)
        join_list = join or None
    try:
        if fetch_all:
            items = client.paginate_query(obj, search=search, sort=sort_block, join=join_list)
        else:
            items = client.query_resource(
                obj, search=search, sort=sort_block, join=join_list, start=start, max_=max_
            )
    except Exception as e:
        typer.echo(f"Error querying {obj}: {e}", err=True)
        raise typer.Exit(1)
    items = asof.bound_records(items, "Created", "Modified", what=obj)
    _emit(items, output_json)


@resource_app.command("info")
def resource_info(
    obj: str = typer.Argument(..., help="Deputy object name."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Show a resource object's schema (fields, types, joins)."""
    client = _make_client(read_config(find_config()))
    try:
        item = client.info_resource(obj)
    except Exception as e:
        typer.echo(f"Error fetching schema for {obj}: {e}", err=True)
        raise typer.Exit(1)
    emit_record(item, output_json)


@resource_app.command("create")
def resource_create(
    obj: str = typer.Argument(..., help="Deputy object name."),
    data: Optional[str] = typer.Option(None, "--data", help="Inline JSON object."),
    file: Optional[str] = typer.Option(None, "-f", "--file", help="Path to a JSON file."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Create a record. Body via --data, -f/--file, or stdin."""
    body = _read_data(data, file)
    client = _make_client(read_config(find_config()))
    try:
        item = client.create_resource(obj, body)
    except Exception as e:
        typer.echo(f"Error creating {obj}: {e}", err=True)
        raise typer.Exit(1)
    emit_record(item, output_json)


@resource_app.command("update")
def resource_update(
    obj: str = typer.Argument(..., help="Deputy object name."),
    id: str = typer.Argument(..., help="Record Id."),
    data: Optional[str] = typer.Option(None, "--data", help="Inline JSON object."),
    file: Optional[str] = typer.Option(None, "-f", "--file", help="Path to a JSON file."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Update a record. Body via --data, -f/--file, or stdin."""
    body = _read_data(data, file)
    client = _make_client(read_config(find_config()))
    try:
        item = client.update_resource(obj, id, body)
    except Exception as e:
        typer.echo(f"Error updating {obj} {id}: {e}", err=True)
        raise typer.Exit(1)
    emit_record(item, output_json)


@resource_app.command("delete")
def resource_delete(
    obj: str = typer.Argument(..., help="Deputy object name."),
    id: str = typer.Argument(..., help="Record Id."),
    yes: bool = typer.Option(False, "-y", "--yes", help="Skip the confirmation prompt."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Delete a record. Irreversible; prompts unless --yes is given."""
    if not yes:
        typer.confirm(f"Delete {obj} {id}? This cannot be undone.", abort=True)
    client = _make_client(read_config(find_config()))
    try:
        item = client.delete_resource(obj, id)
    except Exception as e:
        typer.echo(f"Error deleting {obj} {id}: {e}", err=True)
        raise typer.Exit(1)
    if output_json:
        typer.echo(json.dumps(item, indent=2))
        return
    typer.echo(f"Deleted {obj} {id}.")


if __name__ == "__main__":
    app()
