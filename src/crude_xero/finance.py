"""Xero Finance API (finance.xro/1.0) method group over a XeroSession.

One read-only method group for the Finance product. Unlike Accounting (which
wraps a collection under its plural resource key) or Assets (the
``{pagination, items}`` envelope), the Finance API returns endpoint-specific JSON
objects with no uniform list envelope, so each method returns the parsed object
as-is — there is nothing to unwrap. Every endpoint is GET; the product exposes no
writes.

The Finance product is elevated/gated: it needs its own OAuth scopes
(finance.statements.read, finance.cashvalidation.read,
finance.accountingactivity.read, finance.bankstatementsplus.read) which a standard
app grant does not include. The reconciliation-relevant read is
`get_bank_statement_accounting` — the BankStatementsPlus bank-feed/statement-line
endpoint.
"""

from __future__ import annotations

from crude_common import asof
from crude_xero.accounting import asof_clamp_report_params

BASE = "finance"


class FinanceAPI:
    def __init__(self, session):
        self.session = session

    # ------------------------------------------------------------------
    # Cash validation (data-quality check on cash-coded accounts)
    # ------------------------------------------------------------------

    def get_cash_validation(self, params=None):
        """Fetch the cash-validation result (query: balanceDate, etc.).

        Under WORLD_AS_OF the balanceDate is clamped to the cutoff's date (and
        injected when absent); the result is still computed from today's
        ledger, disclosed as such by the clamp helper.
        """
        return self.session._get(BASE, "CashValidation",
                                 params=asof_clamp_report_params(params, inject_date=False))

    # ------------------------------------------------------------------
    # Bank statements (BankStatementsPlus — the reconciliation-relevant read)
    # ------------------------------------------------------------------

    def get_bank_statement_accounting(self, bank_account_id, from_date, to_date,
                                      summary_only=None):
        """Fetch bank-statement accounting for one bank account over a date range.

        The bank-feed/statement-line read: maps to the `bankAccountID`, `fromDate`
        and `toDate` query params (plus optional `summaryOnly`); unset params are
        dropped.
        """
        asof.check_window_start(from_date, "fromDate")
        b = asof.world_as_of()
        if b is not None:
            cap = b.date().isoformat()
            v = asof.parse_stamp(to_date)
            if to_date is None or v is None or v > b:
                to_date = cap
            asof.emit_current_state("these statement lines (a live feed read)")
        params = {
            "bankAccountID": bank_account_id,
            "fromDate": from_date,
            "toDate": to_date,
            "summaryOnly": summary_only,
        }
        params = {k: v for k, v in params.items() if v is not None}
        return self.session._get(BASE, "BankStatementsPlus/statements", params=params)

    # ------------------------------------------------------------------
    # Financial statements
    # ------------------------------------------------------------------

    def get_balance_sheet(self, params=None):
        return self.session._get(BASE, "FinancialStatements/BalanceSheet",
                                 params=asof_clamp_report_params(params, inject_date=False))

    def get_profit_and_loss(self, params=None):
        return self.session._get(BASE, "FinancialStatements/ProfitAndLoss",
                                 params=asof_clamp_report_params(params, inject_date=False))

    def get_cash_flow(self, params=None):
        return self.session._get(BASE, "FinancialStatements/Cashflow",
                                 params=asof_clamp_report_params(params, inject_date=False))

    def get_trial_balance(self, params=None):
        return self.session._get(BASE, "FinancialStatements/TrialBalance",
                                 params=asof_clamp_report_params(params, inject_date=False))

    # ------------------------------------------------------------------
    # Accounting activities
    # ------------------------------------------------------------------

    def get_account_usage(self, params=None):
        return self.session._get(BASE, "AccountingActivities/AccountUsage",
                                 params=asof_clamp_report_params(params, inject_date=False))

    def get_lock_history(self, params=None):
        return self.session._get(BASE, "AccountingActivities/LockHistory",
                                 params=asof_clamp_report_params(params, inject_date=False))

    def get_report_history(self, params=None):
        return self.session._get(BASE, "AccountingActivities/ReportHistory",
                                 params=asof_clamp_report_params(params, inject_date=False))

    def get_user_activities(self, params=None):
        return self.session._get(BASE, "AccountingActivities/UserActivities",
                                 params=asof_clamp_report_params(params, inject_date=False))
