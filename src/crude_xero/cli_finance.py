"""Finance API resource sub-apps for crude-xero (read-only).

`register(app)` attaches the Finance product sub-apps: `cash-validation` (get),
`bank-statement` (get, by bank account and date range), `financial-statement`
(one command per statement: balance-sheet/profit-loss/cash-flow/trial-balance),
and `activity` (one command per accounting activity:
account-usage/lock-history/report-history/user-activities). The whole product is
read-only, so every verb is a fetch rendered by the shared `emit_record`, with
`--json` for the full structure.

The named-statement and named-activity commands mirror the Accounting CLI's
`report` group: one command per name, each building its query from
`--from-date`/`--to-date` plus the repeatable `--param KEY=VALUE` escape hatch (so
endpoint-specific params that lack a typed flag are still reachable). The Finance
product is reached through the `XeroClient.finance` facade group, like
cli_accounting reaches `.accounting`.
"""

from __future__ import annotations

from typing import List, Optional

import typer

from crude_common.output import emit_record

# Friendly command name -> FinanceAPI method, for the two named-endpoint groups.
FINANCIAL_STATEMENTS = {
    "balance-sheet": "get_balance_sheet",
    "profit-loss": "get_profit_and_loss",
    "cash-flow": "get_cash_flow",
    "trial-balance": "get_trial_balance",
}

ACTIVITIES = {
    "account-usage": "get_account_usage",
    "lock-history": "get_lock_history",
    "report-history": "get_report_history",
    "user-activities": "get_user_activities",
}


def _client(*args, **kwargs):
    """The configured Xero client (lazily, to avoid an import cycle with cli)."""
    from crude_xero.cli import _client as _impl

    return _impl(*args, **kwargs)


def _finance():
    """The Finance method group off the configured client facade (`.finance`)."""
    return _client().finance


def _params(date=None, from_date=None, to_date=None, balance_date=None, extra=None):
    """Build a Finance query dict from the typed flags and repeatable --param.

    Drops unset typed flags; each --param is a KEY=VALUE pair appended verbatim,
    so an endpoint param without a dedicated flag is still reachable.
    """
    params = {}
    if date:
        params["date"] = date
    if from_date:
        params["fromDate"] = from_date
    if to_date:
        params["toDate"] = to_date
    if balance_date:
        params["balanceDate"] = balance_date
    for kv in extra or []:
        if "=" not in kv:
            typer.echo(f"Error: --param must be KEY=VALUE, got {kv!r}.", err=True)
            raise typer.Exit(1)
        k, v = kv.split("=", 1)
        params[k] = v
    return params


# ----------------------------------------------------------------------
# register
# ----------------------------------------------------------------------


def register(app: typer.Typer) -> None:
    """Attach the Finance resource sub-apps to the root app."""

    _register_cash_validation(app)
    _register_bank_statement(app)
    _register_financial_statements(app)
    _register_activities(app)


def _register_cash_validation(app: typer.Typer) -> None:
    sub = typer.Typer(help="Xero cash validation (read-only).")
    app.add_typer(sub, name="cash-validation")

    @sub.command("get", help="Show the cash-validation result.")
    def _get(
        balance_date: Optional[str] = typer.Option(
            None, "--balance-date", help="Balance date (balanceDate, YYYY-MM-DD)."
        ),
        param: Optional[List[str]] = typer.Option(
            None, "--param", help="Extra query param as KEY=VALUE (repeatable)."
        ),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
    ):
        params = _params(balance_date=balance_date, extra=param)
        try:
            result = _finance().get_cash_validation(params or None)
        except Exception as e:
            typer.echo(f"Error fetching cash validation: {e}", err=True)
            raise typer.Exit(1)
        emit_record(result, output_json)


def _register_bank_statement(app: typer.Typer) -> None:
    sub = typer.Typer(help="Xero bank-statement accounting (BankStatementsPlus; read-only).")
    app.add_typer(sub, name="bank-statement")

    @sub.command("get", help="Show bank-statement accounting for a bank account over a date range.")
    def _get(
        bank_account: str = typer.Option(
            ..., "--bank-account", help="Bank account id (bankAccountID, GUID)."
        ),
        from_date: str = typer.Option(..., "--from-date", help="Period start (fromDate, YYYY-MM-DD)."),
        to_date: str = typer.Option(..., "--to-date", help="Period end (toDate, YYYY-MM-DD)."),
        summary_only: bool = typer.Option(
            False, "--summary-only", help="Return the summary only (summaryOnly)."
        ),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
    ):
        try:
            result = _finance().get_bank_statement_accounting(
                bank_account, from_date, to_date, summary_only=summary_only or None
            )
        except Exception as e:
            typer.echo(f"Error fetching bank-statement accounting: {e}", err=True)
            raise typer.Exit(1)
        emit_record(result, output_json)


def _register_financial_statements(app: typer.Typer) -> None:
    sub = typer.Typer(help="Xero financial statements (read-only).")
    app.add_typer(sub, name="financial-statement")
    for cmd_name, method in FINANCIAL_STATEMENTS.items():
        _add_param_command(sub, cmd_name, method, f"{cmd_name} financial statement")


def _register_activities(app: typer.Typer) -> None:
    sub = typer.Typer(help="Xero accounting activities (read-only).")
    app.add_typer(sub, name="activity")
    for cmd_name, method in ACTIVITIES.items():
        _add_param_command(sub, cmd_name, method, f"{cmd_name} activity")


def _add_param_command(sub: typer.Typer, cmd_name: str, method: str, label: str) -> None:
    """Add a read command building its query from --date/--from-date/--to-date/--param.

    Shared by the financial-statement and activity groups: each name maps to a
    FinanceAPI method that takes a params dict, mirroring the Accounting CLI's
    named-report commands.
    """

    @sub.command(cmd_name, help=f"Fetch the {label}.")
    def _cmd(
        date: Optional[str] = typer.Option(None, "--date", help="Report date (date, YYYY-MM-DD)."),
        from_date: Optional[str] = typer.Option(None, "--from-date", help="Period start (fromDate)."),
        to_date: Optional[str] = typer.Option(None, "--to-date", help="Period end (toDate)."),
        param: Optional[List[str]] = typer.Option(
            None, "--param", help="Extra query param as KEY=VALUE (repeatable)."
        ),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
    ):
        params = _params(date=date, from_date=from_date, to_date=to_date, extra=param)
        try:
            result = getattr(_finance(), method)(params or None)
        except Exception as e:
            typer.echo(f"Error fetching {label}: {e}", err=True)
            raise typer.Exit(1)
        emit_record(result, output_json)
