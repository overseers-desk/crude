"""Payroll API resource sub-apps for crude-xero.

`register(app)` attaches one sub-`Typer` per Payroll resource: `pay-employee`,
`pay-run` (list/create only), `pay-run-calendar`, `earnings-rate`,
`reimbursement`, `timesheet`, and the read-only singleton `payroll-settings`. The
uniform verbs (list/get/create/update/delete) are built by a local `_resource`
factory — like the Assets CLI's, trimmed and bound to the `XeroClient.payroll`
facade group — because `cli_accounting._resource` is bound to `.accounting` and
creates with PUT, whereas Payroll creates and updates both with POST. `pay-run`
omits get/update because the single-pay-run detail and element update are not
served by the API (405/404), leaving the pay-run resource read-as-a-list only.
`payroll-settings` is a read-only singleton added explicitly. Reads render with
the shared `_emit_list`/`_emit_record`; writes go through `_do_write`/
`_merge_update`, with confirm-before-write.
"""

from __future__ import annotations

from typing import Optional

import typer

from crude_common.cliutil import _do_write, _emit_list, _emit_record, _merge_update, _read_data
from crude_xero.client import PAGE_SIZE


def _client(*args, **kwargs):
    """The configured Xero client (lazily, to avoid an import cycle with cli)."""
    from crude_xero.cli import _client as _impl

    return _impl(*args, **kwargs)


def _payroll(*args, **kwargs):
    """The Payroll method group off the configured client facade (`.payroll`)."""
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


# Table columns per resource (Payroll fields are camelCase, unlike Accounting).
_EMPLOYEE_COLS = [
    ("ID", "employeeID"), ("First", "firstName"), ("Last", "lastName"),
    ("Email", "email"), ("Start", "startDate"), ("End", "endDate"),
]
_PAY_RUN_COLS = [
    ("ID", "payRunID"), ("Calendar", "payrollCalendarID"),
    ("Start", "periodStartDate"), ("End", "periodEndDate"),
    ("Payment", "paymentDate"), ("Status", "payRunStatus"),
    ("Cost", "totalCost"), ("Pay", "totalPay"),
]
_CALENDAR_COLS = [
    ("ID", "payrollCalendarID"), ("Name", "name"), ("Type", "calendarType"),
    ("Start", "periodStartDate"), ("End", "periodEndDate"), ("Payment", "paymentDate"),
]
_EARNINGS_RATE_COLS = [
    ("ID", "earningsRateID"), ("Name", "name"), ("Earnings", "earningsType"),
    ("Rate", "rateType"), ("Units", "typeOfUnits"), ("Current", "currentRecord"),
]
_REIMBURSEMENT_COLS = [
    ("ID", "reimbursementID"), ("Name", "name"),
    ("Account", "accountID"), ("Current", "currentRecord"),
]
_TIMESHEET_COLS = [
    ("ID", "timesheetID"), ("Employee", "employeeID"), ("Calendar", "payrollCalendarID"),
    ("Start", "startDate"), ("End", "endDate"), ("Status", "status"), ("Hours", "totalHours"),
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

    Like the Assets CLI's factory, trimmed to the verbs the Payroll resources
    share and bound to the `.payroll` group. Create POSTs to the collection and
    update is a read-merge-write POST to the element (both Payroll's convention).
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
            _emit_list(items, columns, name, output_json)

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
            _emit_record(item, output_json)

    if create_fn:

        @sub.command("create", help=f"Create {_a(label)} {label} from a JSON body.")
        def _create(
            data: Optional[str] = typer.Option(None, "--data", help=f"{label} object as JSON (or -f / stdin)."),
            file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
            yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
            output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
        ):
            body = _read_data(data, file)
            _do_write(
                lambda: getattr(_payroll(), create_fn)(body),
                f"create {name}",
                confirm=f"Create this {name}?",
                yes=yes,
                output_json=output_json,
            )

    if update_fn:

        @sub.command("update", help=f"Update {_a(label)} {label} (read-merge-write; Payroll POSTs the element).")
        def _update(
            guid: str = typer.Argument(..., help=f"{label} id (GUID) to update."),
            data: Optional[str] = typer.Option(None, "--data", help="Partial JSON overlaying the fetched record."),
            file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON overlay from a file."),
            yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
            output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
        ):
            papi = _payroll()
            _merge_update(
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
            _do_write(
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
    """Attach the Payroll resource sub-apps to the root app."""

    _resource(
        app, "pay-employee", "payroll employee", _EMPLOYEE_COLS,
        list_fn="list_employees", get_fn="get_employee",
        create_fn="create_employee", update_fn="update_employee",
    )

    # Pay runs are list/create only: GET PayRuns/{id} (405) and the element POST
    # (404) are not served, so there is no get/update/delete to expose.
    _resource(
        app, "pay-run", "pay run", _PAY_RUN_COLS,
        list_fn="list_pay_runs", create_fn="create_pay_run",
    )

    _resource(
        app, "pay-run-calendar", "pay run calendar", _CALENDAR_COLS,
        list_fn="list_pay_run_calendars", get_fn="get_pay_run_calendar",
        create_fn="create_pay_run_calendar",
    )

    _resource(
        app, "earnings-rate", "earnings rate", _EARNINGS_RATE_COLS,
        list_fn="list_earnings_rates", get_fn="get_earnings_rate",
        create_fn="create_earnings_rate", update_fn="update_earnings_rate",
    )

    _resource(
        app, "reimbursement", "reimbursement", _REIMBURSEMENT_COLS,
        list_fn="list_reimbursements", get_fn="get_reimbursement",
        create_fn="create_reimbursement", update_fn="update_reimbursement",
    )

    _resource(
        app, "timesheet", "timesheet", _TIMESHEET_COLS,
        list_fn="list_timesheets", get_fn="get_timesheet",
        create_fn="create_timesheet", update_fn="update_timesheet",
        delete_fn="delete_timesheet",
    )

    _register_settings(app)


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
        _emit_record(item, output_json)
