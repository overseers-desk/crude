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

BASE = "finance"


class FinanceAPI:
    def __init__(self, session):
        self.session = session

    # ------------------------------------------------------------------
    # Cash validation (data-quality check on cash-coded accounts)
    # ------------------------------------------------------------------

    def get_cash_validation(self, params=None):
        """Fetch the cash-validation result (query: balanceDate, etc.)."""
        return self.session._get(BASE, "CashValidation", params=params)

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
        return self.session._get(BASE, "FinancialStatements/BalanceSheet", params=params)

    def get_profit_and_loss(self, params=None):
        return self.session._get(BASE, "FinancialStatements/ProfitAndLoss", params=params)

    def get_cash_flow(self, params=None):
        return self.session._get(BASE, "FinancialStatements/Cashflow", params=params)

    def get_trial_balance(self, params=None):
        return self.session._get(BASE, "FinancialStatements/TrialBalance", params=params)

    # ------------------------------------------------------------------
    # Accounting activities
    # ------------------------------------------------------------------

    def get_account_usage(self, params=None):
        return self.session._get(BASE, "AccountingActivities/AccountUsage", params=params)

    def get_lock_history(self, params=None):
        return self.session._get(BASE, "AccountingActivities/LockHistory", params=params)

    def get_report_history(self, params=None):
        return self.session._get(BASE, "AccountingActivities/ReportHistory", params=params)

    def get_user_activities(self, params=None):
        return self.session._get(BASE, "AccountingActivities/UserActivities", params=params)
