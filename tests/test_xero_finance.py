"""Unit tests for the crude-xero Finance (finance.xro/1.0) method group — no network.

Finance is read-only and returns endpoint-specific JSON objects with no list
envelope, so these pin the load-bearing facts: the `finance.xro/1.0` base path, a
handful of endpoint paths (CashValidation, BankStatementsPlus/statements, the
FinancialStatements/* and AccountingActivities/* routes), that every verb is a
GET, and that params pass through verbatim (with the bank-statement helper
mapping its arguments to the bankAccountID/fromDate/toDate query and dropping an
unset summaryOnly). The inner `requests.Session` is monkeypatched, so nothing
reaches the network.
"""

from __future__ import annotations

import time

from crude_xero.client import XeroSession
from crude_xero.finance import FinanceAPI


class _FakeResp:
    """Stands in for a requests.Response: just the attributes _request reads."""

    def __init__(self, payload=None, *, status_code=200, headers=None, content=None):
        self._payload = payload
        self.status_code = status_code
        self.ok = 200 <= status_code < 400
        self.headers = headers or {}
        if content is not None:
            self.content = content
        elif payload is None:
            self.content = b""
        else:
            self.content = b"<body>"  # truthy, so _request calls .json()

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


def _session(tenant_id="TENANT-1"):
    """A session with a far-future token, so _ensure_token never refreshes."""
    return XeroSession(
        "acct", "client-id", "client-secret",
        {"access_token": "ACCESS-1", "expires_at": time.time() + 9999},
        tenant_id=tenant_id,
    )


def _recorder(response):
    """Capture every transport call; return `response` (or response(call) if callable)."""
    calls = []

    def fake(method, url, params=None, json=None, data=None, headers=None):
        call = {"method": method, "url": url, "params": params,
                "json": json, "data": data, "headers": headers}
        calls.append(call)
        return response(call) if callable(response) else response

    return calls, fake


# ----------------------------------------------------------------------
# Cash validation
# ----------------------------------------------------------------------


def test_get_cash_validation_routes_to_path(monkeypatch):
    xs = _session()
    api = FinanceAPI(xs)
    calls, fake = _recorder(_FakeResp({"accounts": []}))
    monkeypatch.setattr(xs.session, "request", fake)

    out = api.get_cash_validation()

    assert out == {"accounts": []}
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/finance.xro/1.0/CashValidation")
    assert calls[0]["params"] is None


def test_get_cash_validation_passes_params(monkeypatch):
    xs = _session()
    api = FinanceAPI(xs)
    calls, fake = _recorder(_FakeResp({"accounts": []}))
    monkeypatch.setattr(xs.session, "request", fake)

    api.get_cash_validation({"balanceDate": "2026-06-30"})

    assert calls[0]["params"] == {"balanceDate": "2026-06-30"}


# ----------------------------------------------------------------------
# Bank statements (BankStatementsPlus)
# ----------------------------------------------------------------------


def test_get_bank_statement_accounting_maps_args_to_query(monkeypatch):
    xs = _session()
    api = FinanceAPI(xs)
    calls, fake = _recorder(_FakeResp({"statementBalances": []}))
    monkeypatch.setattr(xs.session, "request", fake)

    out = api.get_bank_statement_accounting("BANK-1", "2026-01-01", "2026-06-30")

    assert out == {"statementBalances": []}
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/finance.xro/1.0/BankStatementsPlus/statements")
    # summaryOnly unset -> dropped
    assert calls[0]["params"] == {
        "bankAccountID": "BANK-1", "fromDate": "2026-01-01", "toDate": "2026-06-30"}


def test_get_bank_statement_accounting_includes_summary_only(monkeypatch):
    xs = _session()
    api = FinanceAPI(xs)
    calls, fake = _recorder(_FakeResp({}))
    monkeypatch.setattr(xs.session, "request", fake)

    api.get_bank_statement_accounting("BANK-1", "2026-01-01", "2026-06-30", summary_only=True)

    assert calls[0]["params"] == {
        "bankAccountID": "BANK-1", "fromDate": "2026-01-01",
        "toDate": "2026-06-30", "summaryOnly": True}


# ----------------------------------------------------------------------
# Financial statements — each routes under FinancialStatements/* and is a GET
# ----------------------------------------------------------------------


def test_financial_statement_paths(monkeypatch):
    cases = [
        ("get_balance_sheet", "FinancialStatements/BalanceSheet"),
        ("get_profit_and_loss", "FinancialStatements/ProfitAndLoss"),
        ("get_cash_flow", "FinancialStatements/Cashflow"),
        ("get_trial_balance", "FinancialStatements/TrialBalance"),
    ]
    for method, path in cases:
        xs = _session()
        api = FinanceAPI(xs)
        calls, fake = _recorder(_FakeResp({"reportName": method}))
        monkeypatch.setattr(xs.session, "request", fake)

        out = getattr(api, method)({"fromDate": "2026-01-01", "toDate": "2026-06-30"})

        assert out == {"reportName": method}
        assert calls[0]["method"] == "GET"
        assert calls[0]["url"].endswith(f"/finance.xro/1.0/{path}")
        assert calls[0]["params"] == {"fromDate": "2026-01-01", "toDate": "2026-06-30"}


# ----------------------------------------------------------------------
# Accounting activities — each routes under AccountingActivities/* and is a GET
# ----------------------------------------------------------------------


def test_accounting_activity_paths(monkeypatch):
    cases = [
        ("get_account_usage", "AccountingActivities/AccountUsage"),
        ("get_lock_history", "AccountingActivities/LockHistory"),
        ("get_report_history", "AccountingActivities/ReportHistory"),
        ("get_user_activities", "AccountingActivities/UserActivities"),
    ]
    for method, path in cases:
        xs = _session()
        api = FinanceAPI(xs)
        calls, fake = _recorder(_FakeResp({"name": method}))
        monkeypatch.setattr(xs.session, "request", fake)

        out = getattr(api, method)()

        assert out == {"name": method}
        assert calls[0]["method"] == "GET"
        assert calls[0]["url"].endswith(f"/finance.xro/1.0/{path}")
        assert calls[0]["params"] is None
