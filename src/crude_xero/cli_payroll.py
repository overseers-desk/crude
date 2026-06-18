"""Payroll AU API resource sub-apps for crude-xero.

`register(app)` attaches one sub-`Typer` per Payroll AU resource: `pay-employee`,
`pay-run`, `pay-item`, `timesheet`, `leave-application`, `super-fund`,
`payroll-calendar`, the read-only `payslip`, and the read-only singleton
`payroll-settings`. The uniform verbs (list/get/create/update/delete) are built by
a local `_resource` factory — like the Assets CLI's, trimmed and bound to the
`XeroClient.payroll` facade group — because `cli_accounting._resource` is bound to
`.accounting` and creates with PUT, whereas Payroll AU creates and updates both
with POST. The irregular shapes are added explicitly: `pay-item` is a grouped
object (so its `list` renders as a record and its create/update POST the body
straight to `PayItems` with no element), and `payslip`/`payroll-settings` are
read-only. Reads render with the shared `emit_list`/`emit_record`; writes go
through `do_write`/`merge_update`, with confirm-before-write.
"""

from __future__ import annotations

from typing import Optional

import typer

from crude_common.output import emit_list, emit_record
from crude_common.writeio import do_write, merge_update, read_data
from crude_xero.client import PAGE_SIZE


def _client(*args, **kwargs):
    """The configured Xero client (lazily, to avoid an import cycle with cli)."""
    from crude_xero.cli import _client as _impl

    return _impl(*args, **kwargs)


def _payroll(*args, **kwargs):
    """The Payroll AU method group off the configured client facade (`.payroll`)."""
    return _client(*args, **kwargs).payroll


def _a(label: str) -> str:
    """The indefinite article for a resource label, for readable help text."""
    return "an" if label[:1].lower() in "aeiou" else "a"


def _list_hint(items: list, fetch_all: bool, limit: Optional[int]) -> None:
    """Warn (stderr) when a bare list came back a full page, so more likely exist."""
    if not fetch_all and limit is None and len(items) == PAGE_SIZE:
        typer.echo(
            f"Showing the first {PAGE_SIZE}; pass --all for all, or --limit N for more.",
            err=True,
        )


# Table columns per resource (Payroll AU fields are PascalCase, like Accounting).
_EMPLOYEE_COLS = [
    ("ID", "EmployeeID"), ("First", "FirstName"), ("Last", "LastName"),
    ("Email", "Email"), ("Status", "Status"), ("Start", "StartDate"),
]
_PAY_RUN_COLS = [
    ("ID", "PayRunID"), ("Calendar", "PayrollCalendarID"),
    ("Start", "PayRunPeriodStartDate"), ("End", "PayRunPeriodEndDate"),
    ("Payment", "PaymentDate"), ("Status", "PayRunStatus"),
]
_TIMESHEET_COLS = [
    ("ID", "TimesheetID"), ("Employee", "EmployeeID"),
    ("Start", "StartDate"), ("End", "EndDate"),
    ("Status", "Status"), ("Hours", "Hours"),
]
_LEAVE_COLS = [
    ("ID", "LeaveApplicationID"), ("Employee", "EmployeeID"),
    ("Type", "LeaveTypeID"), ("PayOut", "PayOutType"),
    ("Start", "StartDate"), ("End", "EndDate"),
]
_SUPER_FUND_COLS = [
    ("ID", "SuperFundID"), ("Name", "Name"), ("Type", "Type"),
    ("ABN", "ABN"), ("BSB", "BSB"),
]
_CALENDAR_COLS = [
    ("ID", "PayrollCalendarID"), ("Name", "Name"), ("Type", "CalendarType"),
    ("Start", "StartDate"), ("Payment", "PaymentDate"),
]


def _resource(
    app: typer.Typer,
    name: str,
    label: str,
    columns: list,
    *,
    list_fn: Optional[str] = None,
    get_fn: Optional[str] = None,
    create_fn: Optional[str] = None,
    update_fn: Optional[str] = None,
    delete_fn: Optional[str] = None,
) -> typer.Typer:
    """Create a resource sub-app with the standard verbs and return it.

    Like the Assets CLI's factory, trimmed to the verbs the Payroll AU resources
    share and bound to the `.payroll` group. Create POSTs to the collection and
    update is a read-merge-write POST to the element (both Payroll AU's convention).
    Irregular verbs are added to the returned sub-app by the caller.
    """
    sub = typer.Typer(help=f"Xero {label}.")
    app.add_typer(sub, name=name)

    if list_fn:

        @sub.command("list", help=f"List {label}.")
        def _list(
            fetch_all: bool = typer.Option(False, "--all", help="Fetch every page (default: the first page only)."),
            limit: Optional[int] = typer.Option(None, "--limit", help="Fetch up to N records across pages (--all wins)."),
            output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
        ):
            try:
                items = getattr(_payroll(), list_fn)(
                    all_pages=fetch_all, limit=None if fetch_all else limit)
            except Exception as e:
                typer.echo(f"Error fetching {label}: {e}", err=True)
                raise typer.Exit(1)
            _list_hint(items, fetch_all, limit)
            emit_list(items, columns, name, output_json)

    if get_fn:

        @sub.command("get", help=f"Show a single {label}.")
        def _get(
            guid: str = typer.Argument(..., help=f"{label} id (GUID)."),
            output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
        ):
            try:
                item = getattr(_payroll(), get_fn)(guid)
            except Exception as e:
                typer.echo(f"Error fetching {label} {guid}: {e}", err=True)
                raise typer.Exit(1)
            emit_record(item, output_json)

    if create_fn:

        @sub.command("create", help=f"Create {_a(label)} {label} from a JSON body.")
        def _create(
            data: Optional[str] = typer.Option(None, "--data", help=f"{label} object as JSON (or -f / stdin)."),
            file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
            yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
            output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
        ):
            body = read_data(data, file)
            do_write(
                lambda: getattr(_payroll(), create_fn)(body),
                f"create {name}",
                confirm=f"Create this {name}?",
                yes=yes,
                output_json=output_json,
            )

    if update_fn:

        @sub.command("update", help=f"Update {_a(label)} {label} (read-merge-write; Payroll AU POSTs the element).")
        def _update(
            guid: str = typer.Argument(..., help=f"{label} id (GUID) to update."),
            data: Optional[str] = typer.Option(None, "--data", help="Partial JSON overlaying the fetched record."),
            file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON overlay from a file."),
            yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
            output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
        ):
            papi = _payroll()
            merge_update(
                lambda: getattr(papi, get_fn)(guid),
                lambda merged: getattr(papi, update_fn)(guid, merged),
                data,
                file,
                {},
                f"update {name} {guid}",
                yes,
                output_json,
            )

    if delete_fn:

        @sub.command("delete", help=f"Delete {_a(label)} {label} by id.")
        def _delete(
            guid: str = typer.Argument(..., help=f"{label} id (GUID)."),
            yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
            output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
        ):
            do_write(
                lambda: getattr(_payroll(), delete_fn)(guid),
                f"delete {name} {guid}",
                confirm=f"Delete {name} {guid}?",
                yes=yes,
                output_json=output_json,
            )

    return sub


# ----------------------------------------------------------------------
# register
# ----------------------------------------------------------------------


def register(app: typer.Typer) -> None:
    """Attach the Payroll AU resource sub-apps to the root app."""

    _resource(
        app, "pay-employee", "payroll employee", _EMPLOYEE_COLS,
        list_fn="list_employees", get_fn="get_employee",
        create_fn="create_employee", update_fn="update_employee",
    )

    _resource(
        app, "pay-run", "pay run", _PAY_RUN_COLS,
        list_fn="list_pay_runs", get_fn="get_pay_run",
        create_fn="create_pay_run", update_fn="update_pay_run",
    )

    _register_pay_item(app)

    _resource(
        app, "timesheet", "timesheet", _TIMESHEET_COLS,
        list_fn="list_timesheets", get_fn="get_timesheet",
        create_fn="create_timesheet", update_fn="update_timesheet",
        delete_fn="delete_timesheet",
    )

    _resource(
        app, "leave-application", "leave application", _LEAVE_COLS,
        list_fn="list_leave_applications", get_fn="get_leave_application",
        create_fn="create_leave_application", update_fn="update_leave_application",
    )

    _resource(
        app, "super-fund", "super fund", _SUPER_FUND_COLS,
        list_fn="list_super_funds", get_fn="get_super_fund",
        create_fn="create_super_fund", update_fn="update_super_fund",
    )

    _resource(
        app, "payroll-calendar", "payroll calendar", _CALENDAR_COLS,
        list_fn="list_payroll_calendars", get_fn="get_payroll_calendar",
        create_fn="create_payroll_calendar",
    )

    _register_payslip(app)
    _register_settings(app)


def _register_pay_item(app: typer.Typer) -> None:
    """The `pay-item` sub-app: a grouped-object list, plus POST-to-PayItems writes.

    PayItems is not a flat paged collection but a single object keyed by category
    (EarningsRates, DeductionTypes, LeaveTypes, ReimbursementTypes), so `list`
    renders it as a record (use --json for the categories' contents). It has no
    element path, so create and update both POST the body straight to PayItems.
    """
    pay_item = typer.Typer(help="Xero pay items (a grouped object by category).")
    app.add_typer(pay_item, name="pay-item")

    @pay_item.command("list", help="Show the pay items, grouped by category (use --json for the entries).")
    def _list(
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
    ):
        try:
            item = _payroll().list_pay_items()
        except Exception as e:
            typer.echo(f"Error fetching pay items: {e}", err=True)
            raise typer.Exit(1)
        emit_record(item, output_json)

    @pay_item.command("create", help="Create a pay item from a JSON body (the category array, e.g. EarningsRates).")
    def _create(
        data: Optional[str] = typer.Option(None, "--data", help="Pay item(s) as JSON (or -f / stdin)."),
        file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
    ):
        body = read_data(data, file)
        do_write(
            lambda: _payroll().create_pay_item(body),
            "create pay item", confirm="Create this pay item?",
            yes=yes, output_json=output_json,
        )

    @pay_item.command("update", help="Update a pay item (POSTs the whole category array to PayItems; no element id).")
    def _update(
        data: Optional[str] = typer.Option(None, "--data", help="Pay item(s) as JSON (or -f / stdin)."),
        file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
    ):
        body = read_data(data, file)
        do_write(
            lambda: _payroll().update_pay_item(body),
            "update pay item", confirm="Update this pay item?",
            yes=yes, output_json=output_json,
        )


def _register_payslip(app: typer.Typer) -> None:
    """The read-only `payslip` sub-app (get by id only)."""
    payslip = typer.Typer(help="Xero payslips (read-only).")
    app.add_typer(payslip, name="payslip")

    @payslip.command("get", help="Show a single payslip.")
    def _get(
        guid: str = typer.Argument(..., help="Payslip id (GUID)."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
    ):
        try:
            item = _payroll().get_payslip(guid)
        except Exception as e:
            typer.echo(f"Error fetching payslip {guid}: {e}", err=True)
            raise typer.Exit(1)
        emit_record(item, output_json)


def _register_settings(app: typer.Typer) -> None:
    """The read-only `payroll-settings` sub-app (singleton get)."""
    settings = typer.Typer(help="Xero payroll settings (read-only singleton).")
    app.add_typer(settings, name="payroll-settings")

    @settings.command("get", help="Show the payroll settings.")
    def _get(
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
    ):
        try:
            item = _payroll().get_settings()
        except Exception as e:
            typer.echo(f"Error fetching payroll settings: {e}", err=True)
            raise typer.Exit(1)
        emit_record(item, output_json)
